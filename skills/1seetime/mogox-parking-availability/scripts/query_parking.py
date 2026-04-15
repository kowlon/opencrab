#!/usr/bin/env python3
"""
Query parking availability API and return results.
"""
import argparse
import json
import os
from pathlib import Path

import requests

API_ENDPOINT = "https://mogoc.zhidaozhixing.com/imogo/api/parking/poi"
REQUEST_HEADERS = {
    "validate-white": "9V3z4xhp21MuwFQybf7nHrZteU0RqilYLsBNKcJTP5oCkX6WvI8OmdAEDjgaSGQe",
    "validate-version": "1.0.0",
    "Content-Type": "application/json"
}


def save_json(filepath, data):
    """Save data as JSON to the specified file path."""
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def query_parking(parking_name_query=None, parking_id=None, city_hint=None,
                   user_location=None, location=None, radius=None):
    """Query the parking API with given parameters.

    Two modes:
    - Name mode: uses parkingNameQuery (+ optional cityHint)
    - Nearby mode: uses location (lat/lng) + radius + cityHint, no parkingNameQuery
    """
    payload = {}

    if location:
        # Nearby mode
        payload['location'] = location
        if radius is not None:
            payload['radius'] = radius
    else:
        # Name mode
        if parking_id:
            payload['parkingId'] = parking_id
        if parking_name_query:
            payload['parkingNameQuery'] = parking_name_query

    if city_hint:
        payload['cityHint'] = city_hint
    if user_location:
        payload['userLocation'] = user_location

    try:
        response = requests.post(
            API_ENDPOINT,
            headers=REQUEST_HEADERS,
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {
            "error": str(e),
            "data": []
        }


def main():
    parser = argparse.ArgumentParser(description='Query parking availability API')
    parser.add_argument('--query', help='Parking lot name or keywords')
    parser.add_argument('--parking-id', help='Parking lot ID')
    parser.add_argument('--city-hint', help='City name hint (e.g., "绍兴市")')
    parser.add_argument('--lat', type=float, help='Latitude for nearby search')
    parser.add_argument('--lng', type=float, help='Longitude for nearby search')
    parser.add_argument('--radius', type=int, default=2000, help='Search radius in meters (default: 2000)')
    parser.add_argument('--output', required=True, help='Output JSON file path for API response')

    args = parser.parse_args()

    output_path = Path(args.output)
    task_dir = str(output_path.parent)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Determine search mode
    nearby_mode = args.lat is not None and args.lng is not None

    # Save query params
    query_params = {
        "search_mode": "nearby" if nearby_mode else "name",
        "parking_name_query": args.query,
        "parking_id": args.parking_id,
        "city_hint": args.city_hint
    }
    if nearby_mode:
        query_params["lat"] = args.lat
        query_params["lng"] = args.lng
        query_params["radius"] = args.radius
    save_json(os.path.join(task_dir, "query_params.json"), query_params)

    if nearby_mode:
        result = query_parking(
            city_hint=args.city_hint,
            location={"lat": args.lat, "lng": args.lng},
            radius=args.radius
        )
    else:
        result = query_parking(
            parking_name_query=args.query,
            parking_id=args.parking_id,
            city_hint=args.city_hint
        )

    # Save API response
    save_json(str(output_path), result)

    # Print API response
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
