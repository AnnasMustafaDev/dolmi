// dolmi-audio — streams everything the Mac plays (all apps, any output device) to stdout.
//
// macOS 14.2+ Core Audio process tap: the Mac counterpart of WASAPI loopback on Windows. You keep
// hearing the meeting, no virtual audio cable is needed, and there is no screen-recording indicator.
// The first launch asks for "System Audio Recording" permission (Privacy & Security).
//
// Output: one text line "DOLMI-AUDIO <sample rate> 1\n", then raw little-endian float32 mono samples.
// Stops when stdout closes (Dolmi stopped listening), when Dolmi exits or crashes, or on SIGTERM/SIGINT.
//
// Build: swiftc -O dolmi-audio.swift -o dolmi-audio
import AudioToolbox
import CoreAudio
import Foundation

setvbuf(stdout, nil, _IONBF, 0)
signal(SIGPIPE, SIG_IGN)

func fail(_ msg: String, _ status: OSStatus = 0) -> Never {
    FileHandle.standardError.write("dolmi-audio: \(msg)\(status != 0 ? " (OSStatus \(status))" : "")\n".data(using: .utf8)!)
    exit(1)
}

func prop<T>(_ obj: AudioObjectID, _ selector: AudioObjectPropertySelector, _ value: inout T) -> OSStatus {
    var addr = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<T>.size)
    return withUnsafeMutablePointer(to: &value) { AudioObjectGetPropertyData(obj, &addr, 0, nil, &size, $0) }
}

// 1. a private tap on the mix of every process's audio; the user still hears it (unmuted)
let tap = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
tap.uuid = UUID()
tap.name = "Dolmi"
tap.isPrivate = true
tap.muteBehavior = .unmuted
var tapID = AudioObjectID(kAudioObjectUnknown)
var st = AudioHardwareCreateProcessTap(tap, &tapID)
if st != noErr { fail("could not create the system audio tap", st) }

var format = AudioStreamBasicDescription()
st = prop(tapID, kAudioTapPropertyFormat, &format)
if st != noErr { fail("could not read the tap format", st) }

// 2. the tap is read through a private aggregate device clocked by the current output device
var outputID = AudioObjectID(kAudioObjectUnknown)
st = prop(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDefaultSystemOutputDevice, &outputID)
if st != noErr { fail("no output device", st) }
var uidRef: Unmanaged<CFString>?
st = prop(outputID, kAudioDevicePropertyDeviceUID, &uidRef)
guard st == noErr, let uidRef else { fail("could not read the output device", st) }
let outputUID = uidRef.takeRetainedValue()

let aggregate: [String: Any] = [
    kAudioAggregateDeviceNameKey: "Dolmi system audio",
    kAudioAggregateDeviceUIDKey: UUID().uuidString,
    kAudioAggregateDeviceMainSubDeviceKey: outputUID as String,
    kAudioAggregateDeviceIsPrivateKey: true,
    kAudioAggregateDeviceIsStackedKey: false,
    kAudioAggregateDeviceTapAutoStartKey: true,
    kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outputUID as String]],
    kAudioAggregateDeviceTapListKey: [[kAudioSubTapDriftCompensationKey: true,
                                       kAudioSubTapUIDKey: tap.uuid.uuidString]],
]
var aggID = AudioObjectID(kAudioObjectUnknown)
st = AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &aggID)
if st != noErr { AudioHardwareDestroyProcessTap(tapID); fail("could not create the capture device", st) }

func cleanup() {
    AudioHardwareDestroyAggregateDevice(aggID)
    AudioHardwareDestroyProcessTap(tapID)
}

// 3. downmix every buffer to mono float32 and write it out
let rate = format.mSampleRate > 0 ? format.mSampleRate : 48000
let header = "DOLMI-AUDIO \(Int(rate)) 1\n"
FileHandle.standardOutput.write(header.data(using: .utf8)!)

let queue = DispatchQueue(label: "dolmi.audio")
var procID: AudioDeviceIOProcID?
st = AudioDeviceCreateIOProcIDWithBlock(&procID, aggID, queue) { _, inData, _, _, _ in
    let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: inData))
    guard let first = buffers.first, let p0 = first.mData else { return }
    var mono: [Float]
    if buffers.count == 1 {                       // interleaved: n frames x channels
        let ch = Int(max(first.mNumberChannels, 1))
        let frames = Int(first.mDataByteSize) / (4 * ch)
        let s = p0.assumingMemoryBound(to: Float.self)
        mono = [Float](repeating: 0, count: frames)
        for i in 0..<frames {
            var sum: Float = 0
            for c in 0..<ch { sum += s[i * ch + c] }
            mono[i] = sum / Float(ch)
        }
    } else {                                      // one buffer per channel
        let frames = Int(first.mDataByteSize) / 4
        mono = [Float](repeating: 0, count: frames)
        for b in buffers {
            guard let d = b.mData else { continue }
            let s = d.assumingMemoryBound(to: Float.self)
            for i in 0..<frames { mono[i] += s[i] }
        }
        let n = Float(buffers.count)
        for i in 0..<frames { mono[i] /= n }
    }
    let ok = mono.withUnsafeBytes { fwrite($0.baseAddress, 1, $0.count, stdout) == $0.count }
    if !ok { cleanup(); exit(0) }                 // Dolmi closed the pipe: stop listening
}
if st != noErr { cleanup(); fail("could not attach to the capture device", st) }
st = AudioDeviceStart(aggID, procID)
if st != noErr { cleanup(); fail("could not start capturing", st) }

for sig in [SIGTERM, SIGINT] {
    signal(sig, SIG_IGN)
    let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    src.setEventHandler { AudioDeviceStop(aggID, procID); cleanup(); exit(0) }
    src.resume()
    _ = Unmanaged.passRetained(src)
}
// Dolmi's own children (e.g. multiprocessing's resource tracker) can keep the pipe open after Dolmi
// exits, so also watch the parent process itself: never keep capturing after Dolmi is gone.
let parent = getppid()
let parentWatch = DispatchSource.makeProcessSource(identifier: parent, eventMask: .exit, queue: .main)
parentWatch.setEventHandler { AudioDeviceStop(aggID, procID); cleanup(); exit(0) }
parentWatch.resume()
if getppid() != parent || parent == 1 { cleanup(); exit(0) }   // parent died before the watch began

dispatchMain()
