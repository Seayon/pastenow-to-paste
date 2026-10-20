import CoreData
// Creates artificial data using a locally installed model. No vendor model is distributed.
import Foundation

let output = URL(fileURLWithPath: CommandLine.arguments[1])
let modelURL = URL(fileURLWithPath: CommandLine.arguments[2])
let model = NSManagedObjectModel(contentsOf: modelURL)!
let coordinator = NSPersistentStoreCoordinator(managedObjectModel: model)
let store = try coordinator.addPersistentStore(
  ofType: NSSQLiteStoreType,
  configurationName: nil, at: output.appendingPathComponent("db.sqlite"),
  options: [NSPersistentHistoryTrackingKey: true])
let context = NSManagedObjectContext(concurrencyType: .mainQueueConcurrencyType)
context.persistentStoreCoordinator = coordinator
func create(_ entity: String, _ values: [String: Any]) -> NSManagedObject {
  let object = NSManagedObject(entity: model.entitiesByName[entity]!, insertInto: context)
  for (key, value) in values { object.setValue(value, forKey: key) }
  return object
}
let timestamp = Date(timeIntervalSinceReferenceDate: 700000000.125)
let device = create(
  "DeviceEntity",
  [
    "identifier": "synthetic-device", "name": "Test Mac",
    "rawType": 0, "createdAt": timestamp,
  ])
let list = create(
  "ListEntity",
  [
    "identifier": "sharedPasteboardHistory", "name": "Clipboard",
    "rawType": 1, "createdAt": timestamp,
    "rawAttributes": Data("{\"type\":\"clipboard\",\"capacity\":30}".utf8),
  ])
let metadata = create(
  "ListMetadataEntity",
  [
    "identifier": "list:sharedPasteboardHistory",
    "index": 0, "createdAt": timestamp,
  ])
list.setValue(metadata, forKey: "metadata")
let original = create(
  "ItemEntity",
  [
    "identifier": "original-synthetic-record",
    "checksum": "original-checksum", "rawType": 5, "createdAt": timestamp,
    "timestamp": timestamp, "title": "Original synthetic title",
    "rawPreview": Data("{\"type\":\"text\",\"text\":\"Original\",\"textLength\":8}".utf8),
  ])
let payload: [[String: Any]] = [
  [
    "types": ["public.utf8-plain-text"],
    "dataByType": ["public.utf8-plain-text": Data("Original".utf8)],
  ]
]
let data = create(
  "ItemDataEntity",
  [
    "rawPasteboardItems": try PropertyListSerialization.data(
      fromPropertyList: payload, format: .binary, options: 0)
  ])
original.setValue(data, forKey: "data")
original.setValue(device, forKey: "device")
original.setValue(list, forKey: "list")
try context.save()
try coordinator.remove(store)
