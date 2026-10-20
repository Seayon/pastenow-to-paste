import AppKit
import CoreData
import CryptoKit
import Foundation
import ImageIO

guard CommandLine.arguments.count == 3 else {
  fputs("Usage: import-paste WORKSPACE PASTE_APP\n", stderr)
  exit(2)
}
let base = URL(fileURLWithPath: CommandLine.arguments[1])
let app = URL(fileURLWithPath: CommandLine.arguments[2])
let storeURL = base.appendingPathComponent("prepared/Paste/db.sqlite")
let export =
  try JSONSerialization.jsonObject(
    with: Data(
      contentsOf:
        base.appendingPathComponent("export/records.json"))) as! [String: Any]
let items = export["items"] as! [[String: Any]]
let sourceApps = export["applications"] as! [[String: Any]]
let namespace = export["namespace"] as! String
let modelURL = app.appendingPathComponent(
  "Contents/Resources/PastePackages_PasteCore.bundle/Contents/Resources/v5Paste.momd/v11.mom")
guard let model = NSManagedObjectModel(contentsOf: modelURL) else {
  fputs("Cannot load the validated Paste model\n", stderr)
  exit(1)
}
let metadata = try NSPersistentStoreCoordinator.metadataForPersistentStore(
  ofType: NSSQLiteStoreType, at: storeURL, options: nil)
guard model.isConfiguration(withName: nil, compatibleWithStoreMetadata: metadata) else {
  fputs("Paste database is incompatible with the installed model\n", stderr)
  exit(1)
}
let psc = NSPersistentStoreCoordinator(managedObjectModel: model)
let store = try psc.addPersistentStore(
  ofType: NSSQLiteStoreType, configurationName: nil, at: storeURL,
  options: [
    NSPersistentHistoryTrackingKey: true,
    NSPersistentStoreRemoteChangeNotificationPostOptionKey: true,
  ])
let ctx = NSManagedObjectContext(concurrencyType: .mainQueueConcurrencyType)
ctx.persistentStoreCoordinator = psc
ctx.transactionAuthor = "PasteNowToPaste"
ctx.name = "PasteNowMigration"
ctx.mergePolicy = NSMergeByPropertyObjectTrumpMergePolicy
func fetch(_ entity: String) throws -> [NSManagedObject] {
  try ctx.fetch(NSFetchRequest<NSManagedObject>(entityName: entity))
}
func new(_ entity: String) -> NSManagedObject {
  NSManagedObject(entity: model.entitiesByName[entity]!, insertInto: ctx)
}
func set(_ o: NSManagedObject, _ values: [String: Any]) {
  for (k, v) in values { o.setValue(v, forKey: k) }
}
func plist(_ value: Any) throws -> Data {
  try PropertyListSerialization.data(fromPropertyList: value, format: .binary, options: 0)
}
func md5(_ d: Data) -> String {
  Insecure.MD5.hash(data: d).map { String(format: "%02x", $0) }.joined()
}
func dataJSON(_ v: Any) throws -> Data {
  try JSONSerialization.data(withJSONObject: v, options: [.sortedKeys])
}
func thumb(_ data: Data) -> (Data, [Double])? {
  guard let source = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
  let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]
  let w = (props?[kCGImagePropertyPixelWidth] as? NSNumber)?.doubleValue ?? 0
  let h = (props?[kCGImagePropertyPixelHeight] as? NSNumber)?.doubleValue ?? 0
  let options: [CFString: Any] = [
    kCGImageSourceCreateThumbnailFromImageAlways: true, kCGImageSourceThumbnailMaxPixelSize: 600,
    kCGImageSourceCreateThumbnailWithTransform: true,
  ]
  guard let cg = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary),
    let png = NSBitmapImageRep(cgImage: cg).representation(using: .png, properties: [:])
  else { return nil }
  return (png, [w, h])
}
var existing = Set(try fetch("ItemEntity").compactMap { $0.value(forKey: "identifier") as? String })
var apps = Dictionary(
  uniqueKeysWithValues: try fetch("ApplicationEntity").map {
    ($0.value(forKey: "bundleIdentifier") as! String, $0)
  })
let existingLists = try fetch("ListEntity")
guard
  let clipboard = existingLists.first(where: {
    $0.value(forKey: "identifier") as? String == "sharedPasteboardHistory"
  }), let device = try fetch("DeviceEntity").first
else {
  fputs("Open Paste once to initialize its history and device, then quit it\n", stderr)
  exit(1)
}
let attributes =
  try JSONSerialization.jsonObject(
    with:
      clipboard.value(forKey: "rawAttributes") as! Data) as! [String: Any]
var unlimited = attributes
unlimited["capacity"] = 0
clipboard.setValue(try dataJSON(unlimited), forKey: "rawAttributes")

var listMap = [Int: NSManagedObject]()
let now = Date()
let sourceLists = export["lists"] as! [String: [String: Any]]
let manual = sourceLists.compactMap { (k, v) -> (Int, [String: Any])? in
  (v["type"] as? Int) == 0 ? (Int(k)!, v) : nil
}.sorted { ($0.1["sort_order"] as? Int ?? 0) < ($1.1["sort_order"] as? Int ?? 0) }
for (idx, pair) in manual.enumerated() {
  let (key, source) = pair
  let ident = "pastenow-list-\(namespace)-\(key)"
  if let found = existingLists.first(where: { $0.value(forKey: "identifier") as? String == ident })
  {
    listMap[key] = found
    continue
  }
  let l = new("ListEntity")
  let m = new("ListMetadataEntity")
  set(
    l,
    [
      "identifier": ident, "name": source["name"] as? String ?? "PasteNow", "rawType": 2,
      "createdAt": now,
      "rawAttributes": try dataJSON(["type": "pinboard", "colorCode": 15_750_477]),
    ])
  set(m, ["identifier": "list:\(ident)", "index": existingLists.count + idx, "createdAt": now])
  l.setValue(m, forKey: "metadata")
  listMap[key] = l
}
var report = [[String: Any]]()
var count = 0
for item in items {
  try autoreleasepool {
    let sid = item["source_id"] as! Int
    let ident = item["identifier"] as! String
    if existing.contains(ident) { return }
    let parts = item["pasteboard_parts"] as! [[String: Any]]
    var groups = [[String: Any]]()
    var types = [String]()
    var payload = [String: Data]()
    var allPayload = [(String, Data)]()
    for p in parts {
      let t = p["type"] as! String
      let d = try Data(
        contentsOf: base.appendingPathComponent("export").appendingPathComponent(
          p["path"] as! String))
      if payload[t] != nil {
        groups.append(["types": types, "dataByType": payload])
        types = []
        payload = [:]
      }
      types.append(t)
      payload[t] = d
      allPayload.append((t, d))
    }
    if !payload.isEmpty { groups.append(["types": types, "dataByType": payload]) }
    let sourceType = item["type"] as! String
    let text = item["text"] as? String ?? ""
    var rawType = 5
    var preview: [String: Any] = ["type": "text", "text": text, "textLength": text.count]
    var primary = allPayload.first?.1 ?? Data()
    if sourceType == "public.image" {
      rawType = 1
      let imageTypes = [
        "public.png", "public.tiff", "public.jpeg", "public.image", "com.apple.icns",
      ]
      let candidates =
        allPayload.filter { imageTypes.contains($0.0) }
        + allPayload.filter { !imageTypes.contains($0.0) }
      var found = false
      for (_, d) in candidates {
        if let (png, size) = thumb(d) {
          preview = ["type": "image", "imageData": png, "imageSize": size]
          primary = d
          found = true
          break
        }
      }
      if !found {
        throw NSError(
          domain: "Migration", code: 1,
          userInfo: [NSLocalizedDescriptionKey: "image_decode_failed_\(sid)"])
      }
    } else if sourceType == "public.file-url" {
      rawType = 3
      let paths = allPayload.filter { $0.0 == "public.file-url" }.compactMap {
        String(data: $0.1, encoding: .utf8)
      }.compactMap { URL(string: $0)?.path }
      var total: Int64 = 0
      for path in paths {
        if let n = (try? FileManager.default.attributesOfItem(atPath: path)[.size]) as? NSNumber {
          total += n.int64Value
        }
      }
      var imageData: Data?
      for (_, d) in allPayload.filter({
        ["public.png", "public.tiff", "public.jpeg", "public.image"].contains($0.0)
      }) {
        if let result = thumb(d) {
          imageData = result.0
          break
        }
      }
      if imageData == nil {
        let icon = NSWorkspace.shared.icon(forFile: paths.first ?? "/PasteNow-missing-file")
        if let tiff = icon.tiffRepresentation { imageData = thumb(tiff)?.0 }
      }
      preview = ["type": "files", "filePaths": paths, "filesTotalSize": total]
      if let png = imageData { preview["filesImageData"] = png }
      primary = Data(paths.joined(separator: "\n").utf8)
    } else if sourceType == "public.url", let u = URL(string: text), u.scheme != nil {
      rawType = 4
      preview = [
        "type": "link", "url": u.absoluteString, "urlName": item["title"] as? String ?? "",
      ]
      primary = allPayload.first(where: { $0.0 == "public.utf8-plain-text" })?.1 ?? Data(text.utf8)
      // PasteNow sometimes stores URL clips as text only; retain original representations.
    } else {
      if let d = allPayload.first(where: { $0.0 == "public.utf8-plain-text" })?.1 { primary = d }
      if text.isEmpty {
        preview = [
          "type": "text", "text": "PasteNow special-format record (original data preserved)",
          "textLength": 0,
        ]
      }
    }
    let raw = try plist(groups)
    let pd = try plist(preview)
    let ts = Date(
      timeIntervalSinceReferenceDate: (item["source_timestamp"] as! NSNumber).doubleValue)
    let o = new("ItemEntity")
    let d = new("ItemDataEntity")
    let listID = item["list_id"] as? Int ?? 0
    let title = item["title"] as? String
    set(
      o,
      [
        "identifier": ident, "checksum": md5(primary), "rawType": rawType, "createdAt": ts,
        "timestamp": ts, "rawPreview": pd,
        "displayOrderInPinboard": item["sort_order"] as? Int ?? 0,
      ])
    if let title = title, !title.isEmpty { o.setValue(title, forKey: "title") }
    if let up = item["source_updated_at"] as? NSNumber {
      o.setValue(Date(timeIntervalSinceReferenceDate: up.doubleValue), forKey: "updatedAt")
    }
    d.setValue(raw, forKey: "rawPasteboardItems")
    o.setValue(d, forKey: "data")
    o.setValue(device, forKey: "device")
    o.setValue(listMap[listID] ?? clipboard, forKey: "list")
    let sourceID = item["source_app_id"] as? Int
    let sourceApp = sourceApps.first { $0["id"] as? Int == sourceID }
    let bundle = (sourceApp?["bundle_id"] as? String) ?? (item["source_bundle_id"] as? String) ?? ""
    if !bundle.isEmpty {
      var application = apps[bundle]
      if application == nil {
        let record = new("ApplicationEntity")
        set(
          record,
          [
            "bundleIdentifier": bundle,
            "name": sourceApp?["name"] as? String ?? bundle, "createdAt": ts, "rawColor": 0,
          ])
        if let iconPath = sourceApp?["icon_path"] as? String,
          let original = try? Data(
            contentsOf: base.appendingPathComponent("export").appendingPathComponent(iconPath)),
          let icon = thumb(original)?.0
        {
          record.setValue(icon, forKey: "rawIcon")
        }
        apps[bundle] = record
        application = record
      }
      o.setValue(application, forKey: "sourceApplication")
    }
    // Native Core Data writes preserve relationships and create persistent history for indexing/sync.
    try ctx.save()
    existing.insert(ident)
    count += 1
    report.append([
      "source_id": sid, "identifier": ident, "source_timestamp": ts.timeIntervalSinceReferenceDate,
      "raw_type": rawType, "parts": parts.count, "groups": groups.count, "bytes": raw.count,
      "list_id": listID,
    ])
    if count % 100 == 0 {
      print("imported", count)
      fflush(stdout)
    }
  }
}
try ctx.save()
let output = base.appendingPathComponent("import-result.json")
try dataJSON(report).write(to: output)
print("imported", count, "existing_total", existing.count)
try psc.remove(store)
