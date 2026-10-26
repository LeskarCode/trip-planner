# ---------------------------------------------------------------------
# 1. IMPORTS - bringing in tools
# ---------------------------------------------------------------------
import os                  # link to the operating system: read environment settings
import socket              # networking basics: find this machine's/container's name

from bson import ObjectId  # MongoDB's special ID type (comes with pymongo)
from flask import Flask, jsonify, request
#   Flask   = the web server
#   jsonify = turn Python data into JSON for the answer
#   request = read what the caller sent (e.g. the JSON body of a POST)
from pymongo import MongoClient  # connector to MongoDB


# ---------------------------------------------------------------------
# 2. CREATE THE WEB SERVER
# ---------------------------------------------------------------------
app = Flask(__name__)      # all routes below are attached to this "app"


# ---------------------------------------------------------------------
# 3. SETTINGS FROM THE ENVIRONMENT
#    "Use the setting if it exists, otherwise the default."
#    On my PC:       MONGO_HOST = localhost (default)
#    Docker Compose: MONGO_HOST = trip-db (set in docker-compose.yml)
#    Kubernetes:     MONGO_HOST = the database Service's name (set in YAML)
# ---------------------------------------------------------------------
MONGO_HOST = os.environ.get("MONGO_HOST", "localhost")
MONGO_PORT = int(os.environ.get("MONGO_PORT", "27017"))
#   int(...) because environment settings are always text ("27017")


# ---------------------------------------------------------------------
# 4. DATABASE CONNECTION
#
#   client  -> the connection to the MongoDB server
#     └── db "tripplanner"   (created automatically on first use)
#           ├── trips        (collection = like a table)
#           └── stops
# ---------------------------------------------------------------------
client = MongoClient(MONGO_HOST, MONGO_PORT, serverSelectionTimeoutMS=3000)
#   serverSelectionTimeoutMS=3000: give up after 3 seconds if MongoDB
#   doesn't answer, instead of waiting forever (node failure pattern)
db = client["tripplanner"]
trips = db["trips"]
stops = db["stops"]


HOSTNAME = socket.gethostname()
#   Name of this machine/container. Sent back as "served_by" so I can
#   see WHICH copy of the API answered when there are several (scaling).


# ---------------------------------------------------------------------
# 5. HELPERS - turn MongoDB data into clean JSON
#    MongoDB stores "_id" as an ObjectId, which JSON can't handle,
#    so we make a clean copy with "id" as plain text.
# ---------------------------------------------------------------------
def format_trip(trip):
    return {
        "id": str(trip["_id"]),                  # ObjectId -> text
        "name": trip["name"],
        "currency": trip.get("currency", "EUR"), # default EUR if missing
    }


def format_stop(stop):
    return {
        "id": str(stop["_id"]),
        "trip_id": stop["trip_id"],              # which trip it belongs to
        "day": stop["day"],
        "city": stop["city"],
        "title": stop["title"],
        "category": stop.get("category", "other"),  # default "other"
        "cost": stop.get("cost", 0),                # default 0
    }


def find_trip(trip_id):
    # Two checks in a row:
    # 1. is_valid: is the text even SHAPED like a MongoDB ID
    #    (24 characters, 0-9 and a-f)? If not -> False, stop here
    #    (avoids a crash for addresses like /trips/hello).
    # 2. find_one: look the trip up -> returns the trip, or None.
    return ObjectId.is_valid(trip_id) and trips.find_one({"_id": ObjectId(trip_id)})


# ---------------------------------------------------------------------
# 6. ROUTES
#    A route = address + request type -> function.
#    Whatever the function returns is sent back:  (JSON, status code)
#
#    Status codes used:
#      200 OK (default) | 201 Created | 400 Bad request | 404 Not found
# ---------------------------------------------------------------------

# --- 6a. Health: "Are you alive?" ------------------------------------
#     No database, no logic. Kubernetes can call it to check the copy
#     is running and restart it if not (self-healing).
@app.route("/health")
def health_check():
    return jsonify({"status": "ok", "service": "trip-api"})


# --- 6b. GET /trips: list all trips ----------------------------------
#     Read from the inside out:
#     trips.find()  -> all trips from the collection
#     for t in ...  -> one by one
#     format_trip(t)-> clean each one
#     [ ... ]       -> collect into a list
#     jsonify(...)  -> send as JSON, status 200
@app.route("/trips", methods=["GET"])
def get_trips():
    return jsonify([format_trip(t) for t in trips.find()])


# --- 6c. POST /trips: create a trip ----------------------------------
@app.route("/trips", methods=["POST"])
def create_trip():
    trip_data = request.get_json(silent=True) or {}
    #   silent=True: invalid JSON -> None instead of a crash
    #   or {}: turn None into an empty dictionary

    trip_name = (trip_data.get("name") or "").strip()
    #   missing name -> "" ; strip() removes spaces at start/end
    if not trip_name:
        return jsonify({"error": "name is required"}), 400   # stop here

    trip_currency = (trip_data.get("currency") or "EUR").strip().upper()
    #   default EUR, no spaces, capitals ("usd" -> "USD")

    new_trip = {
        "name": trip_name,
        "currency": trip_currency
    }

    result = trips.insert_one(new_trip)
    #   SAVE in MongoDB; MongoDB creates the ID -> result.inserted_id

    response = {
        "id": str(result.inserted_id),
        "name": trip_name,
        "currency": trip_currency
    }

    return jsonify(response), 201
    
# --- 6d. DELETE /trips/<trip_id>: delete a trip ----------------------
@app.route("/trips/<trip_id>", methods=["DELETE"])
def delete_trip(trip_id):
    if not ObjectId.is_valid(trip_id):
        return jsonify({"error": "invalid id"}), 400

    result = trips.delete_one({"_id": ObjectId(trip_id)})

    if result.deleted_count == 0:
        return jsonify({"error": "trip not found"}), 404

    # Remove all stops that belong to the deleted trip.
    stops.delete_many({"trip_id": trip_id})

    return jsonify({"deleted": trip_id})


# --- 6e. GET /trips/<trip_id>/stops: a trip's stops, in day order -----
#     <trip_id> in the address becomes the variable trip_id
@app.route("/trips/<trip_id>/stops", methods=["GET"])
def get_stops(trip_id):
    if not ObjectId.is_valid(trip_id):
        return jsonify({"error": "invalid id"}), 400

    if not find_trip(trip_id):
        return jsonify({"error": "trip not found"}), 404

    found = stops.find({"trip_id": trip_id}).sort("day", 1)
    #   find({...}) = FILTER: only stops of this trip
    #   sort("day", 1) = by day, smallest first (-1 would be largest first)

    return jsonify([format_stop(s) for s in found])


# --- 6f. POST /trips/<trip_id>/stops: add a stop ---------------------
#     Pattern: CHECK EVERYTHING FIRST, SAVE ONLY IF ALL IS FINE,
#     so the database never gets half-wrong data.
@app.route("/trips/<trip_id>/stops", methods=["POST"])
def create_stop(trip_id):
    if not ObjectId.is_valid(trip_id):
        return jsonify({"error": "invalid id"}), 400

    if not find_trip(trip_id):
        return jsonify({"error": "trip not found"}), 404

    stop_data = request.get_json(silent=True) or {}

    try:
        stop_day = int(stop_data.get("day"))           # whole number
        stop_cost = float(stop_data.get("cost", 0))    # may have decimals
    except (TypeError, ValueError):
        # TypeError:  day missing  -> int(None)
        # ValueError: not a number -> int("abc")
        return jsonify({"error": "day and cost must be numbers"}), 400

    if stop_day < 1:
        return jsonify({"error": "day must be at least 1"}), 400

    if stop_cost < 0:
        return jsonify({"error": "cost must be at least 0"}), 400

    stop_city = (stop_data.get("city") or "").strip()
    stop_title = (stop_data.get("title") or "").strip()

    if not stop_city or not stop_title:
        return jsonify({"error": "city and title are required"}), 400

    new_stop = {
        "trip_id": trip_id,
        "day": stop_day,
        "city": stop_city,
        "title": stop_title,
        "category": (stop_data.get("category") or "other").strip().lower(),
        "cost": stop_cost,
    }

    result = stops.insert_one(new_stop)        # SAVE
    new_stop["_id"] = result.inserted_id       # add the new ID so format_stop works

    return jsonify(format_stop(new_stop)), 201


# --- 6g. DELETE /stops/<stop_id>: remove a stop -----------------------
#     ID not shaped like an ID   -> 400
#     shaped right, no such stop -> 404 (deleted_count == 0)
#     found and removed          -> 200
@app.route("/stops/<stop_id>", methods=["DELETE"])
def delete_stop(stop_id):
    if not ObjectId.is_valid(stop_id):
        return jsonify({"error": "invalid id"}), 400

    result = stops.delete_one({"_id": ObjectId(stop_id)})
    #   ObjectId(stop_id): text -> MongoDB's own ID type, so it can be found

    if result.deleted_count == 0:
        return jsonify({"error": "stop not found"}), 404

    return jsonify({"deleted": stop_id})


# --- 6h. GET /trips/<trip_id>/summary: the API's OWN LOGIC -----------
#     Goes through all stops of the trip ONCE and keeps four running
#     totals, like four columns on a sheet of paper:
#
#     stop               total  per_city      per_category   per_day
#     Jazz bar (25)        25   Chicago 25    bar 25         "1": 25
#     Art Inst. (32)       57   Chicago 57    museum 32      "2": 32
#     ...
@app.route("/trips/<trip_id>/summary", methods=["GET"])
def get_trip_summary(trip_id):
    if not ObjectId.is_valid(trip_id):
        return jsonify({"error": "invalid id"}), 400

    trip = find_trip(trip_id)       # the trip itself (or None)

    if not trip:
        return jsonify({"error": "trip not found"}), 404

    total = 0.0
    per_city = {}
    per_category = {}
    per_day = {}

    trip_stops = stops.find({"trip_id": trip_id})

    for stop in trip_stops:
        cost = stop.get("cost", 0)
        city = stop["city"]
        category = stop.get("category", "other")
        day = str(stop["day"])

        total = total + cost

        per_city[city] = per_city.get(city, 0) + cost
        #   per_city.get(city, 0): what we have so far for this city,
        #   or 0 if it's the first stop there -> then add this cost

        per_category[category] = per_category.get(category, 0) + cost
        per_day[day] = per_day.get(day, 0) + cost

    summary = {
        "trip": format_trip(trip),
        "total": round(total, 2),     # 2 decimals (avoid 0.30000000000000004)
        "per_city": per_city,
        "per_category": per_category,
        "per_day": per_day,
        "served_by": HOSTNAME,        # which API copy answered (scaling demo)
    }

    return jsonify(summary)


# ---------------------------------------------------------------------
# 7. START-UP (only when run directly with "python app.py" on my PC)
#    In Docker/Kubernetes, gunicorn imports "app" from this file
#    instead (CMD ... "app:app"), so these lines are NOT used there.
#
#    host="0.0.0.0" -> listen on all network addresses (needed in containers)
#    port=5000      -> the API's door number
#    debug=True     -> restart on save + detailed errors (development only!)
#
#    MUST stay the LAST lines of the file; every @app.route goes above.
# ---------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)