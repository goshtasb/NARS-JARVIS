// SummaryPDF (ADR-058) — materializes an archived summary's text into an openable PDF under
// ~/Documents/JARVIS Summaries/. The daemon owns the durable text archive; the PDF is rendered once
// per summary (cached by id) and re-opened thereafter. Native CoreText pagination — no dependency.
import AppKit
import CoreText

enum SummaryPDF {
    /// The on-disk location for a summary's PDF: ~/Documents/JARVIS Summaries/<name>-<id>.pdf.
    static func url(name: String, id: Int) -> URL {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let dir = docs.appendingPathComponent("JARVIS Summaries", isDirectory: true)
        let safe = name.replacingOccurrences(of: "/", with: "-").replacingOccurrences(of: ":", with: "-")
        return dir.appendingPathComponent("\(safe)-\(id).pdf")
    }

    /// Render `text` to a paginated US-Letter PDF and return its URL. Reuses the file if it already
    /// exists (the daemon archive is the source of truth, so each summary is rendered once).
    @discardableResult
    static func write(name: String, id: Int, title: String, text: String) -> URL? {
        let out = url(name: name, id: id)
        try? FileManager.default.createDirectory(at: out.deletingLastPathComponent(),
                                                 withIntermediateDirectories: true)
        if FileManager.default.fileExists(atPath: out.path) { return out }

        let body = NSMutableAttributedString(string: title + "\n\n",
            attributes: [.font: NSFont.boldSystemFont(ofSize: 15), .foregroundColor: NSColor.black])
        body.append(NSAttributedString(string: text,
            attributes: [.font: NSFont.systemFont(ofSize: 11), .foregroundColor: NSColor.black]))

        let page = CGRect(x: 0, y: 0, width: 612, height: 792)          // US Letter
        let textPath = CGPath(rect: page.insetBy(dx: 54, dy: 54), transform: nil)  // 0.75" margins
        let framesetter = CTFramesetterCreateWithAttributedString(body)

        guard let consumer = CGDataConsumer(url: out as CFURL) else { return nil }
        var mediaBox = page
        guard let ctx = CGContext(consumer: consumer, mediaBox: &mediaBox, nil) else { return nil }

        var range = CFRange(location: 0, length: 0)
        let total = body.length
        repeat {
            ctx.beginPDFPage(nil)
            let frame = CTFramesetterCreateFrame(framesetter, range, textPath, nil)
            CTFrameDraw(frame, ctx)
            let drawn = CTFrameGetVisibleStringRange(frame).length
            ctx.endPDFPage()
            if drawn <= 0 { break }                                    // nothing fit -> avoid an infinite loop
            range.location += drawn
        } while range.location < total
        ctx.closePDF()
        return out
    }
}
