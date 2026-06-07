// Microphone capture for push-to-talk. Writes a 16 kHz mono 16-bit WAV (what whisper.cpp wants) to
// $TMPDIR and hands back the path. The mic is the one accepted TCC for the voice feature; the .app
// bundle's NSMicrophoneUsageDescription makes that prompt clean and attributed. The recording is
// transient — the daemon deletes it after transcription.
import AVFoundation
import Foundation

final class AudioRecorder {
    private var recorder: AVAudioRecorder?
    private var url: URL?

    /// Ask for mic permission up front so the first hold-to-talk isn't swallowed by the dialog.
    static func requestPermission() {
        AVCaptureDevice.requestAccess(for: .audio) { _ in }
    }

    var isRecording: Bool { recorder?.isRecording ?? false }

    func start() {
        let path = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("nars-utt-\(UUID().uuidString).wav")
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatLinearPCM),
            AVSampleRateKey: 16000.0,           // whisper.cpp expects 16 kHz
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
        ]
        do {
            let r = try AVAudioRecorder(url: path, settings: settings)
            r.record()
            recorder = r
            url = path
        } catch {
            FileHandle.standardError.write("rec start failed: \(error)\n".data(using: .utf8)!)
        }
    }

    /// Stop and return the finished WAV path (nil if nothing was captured).
    func stop() -> String? {
        guard let r = recorder else { return nil }
        r.stop()
        recorder = nil
        let path = url?.path
        url = nil
        return path
    }
}
