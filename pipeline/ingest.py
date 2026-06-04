import argparse
import json
import requests
from pathlib import Path

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest a generated event JSON file into the Store Intelligence API."
    )
    parser.add_argument(
        "--events",
        default="events.json",
        help="Path to the generated events JSON file.",
    )
    parser.add_argument(
        "--store",
        default="store-1",
        help="Store ID to assign these events to.",
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8000/events/ingest",
        help="The endpoint URL of the running API.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=400,
        help="Number of events to send in each API batch call.",
    )
    return parser

def main() -> None:
    args = build_parser().parse_args()
    
    events_path = Path(args.events)
    if not events_path.exists():
        print(f"Error: Events file not found at {args.events}")
        return

    print(f"Reading events from {args.events}...")
    try:
        events = json.loads(events_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Error reading JSON file: {e}")
        return

    print(f"Loaded {len(events)} events.")
    
    prepared_events = []
    for i, event in enumerate(events):
        metadata = event.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        
        # Inject the custom store ID
        metadata["store_id"] = args.store
        
        prepared_event = {
            "event_type": event.get("event_type"),
            "track_id": event.get("track_id"),
            "frame_number": event.get("frame_number"),
            "timestamp_ms": event.get("timestamp_ms"),
            "occurred_at": event.get("occurred_at"),
            "zone_id": event.get("zone_id"),
            "zone_name": event.get("zone_name"),
            "confidence": event.get("confidence"),
            "metadata": metadata
        }
        prepared_events.append(prepared_event)

    total_events = len(prepared_events)
    batch_size = args.batch_size
    print(f"Ingesting {total_events} events into store '{args.store}' in batches of {batch_size}...")

    success_count = 0
    for i in range(0, total_events, batch_size):
        batch = prepared_events[i:i+batch_size]
        payload = {"events": batch}
        
        try:
            response = requests.post(args.url, json=payload)
            if response.status_code == 200:
                res_data = response.json()
                accepted = res_data.get('accepted', 0)
                success_count += accepted
                print(f"Batch {i//batch_size + 1}: Ingested {accepted}/{len(batch)} events (duplicates: {res_data.get('duplicates', 0)}).")
            else:
                print(f"Batch {i//batch_size + 1}: Failed with status {response.status_code}. Response: {response.text}")
        except Exception as e:
            print(f"Batch {i//batch_size + 1}: Network error: {e}")

    print(f"\nCompleted! Successfully ingested {success_count}/{total_events} events into the database.")

if __name__ == "__main__":
    main()
