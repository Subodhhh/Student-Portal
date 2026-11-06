import json
import random
import uuid
from datetime import datetime, timezone

# New Provider ID
provider_id = "c97e334d-861d-4ed5-817f-e8d35141c8cc"

# The 5 Branches
branches = [
    {"id": "351cf3cb-dea4-410c-b920-bd78bf9fdd52", "name": "Gurukulam"},
    {"id": "b466273b-0250-4681-9f93-1d40057197d1", "name": "Jammu-Akhnoor"},
    {"id": "d3eb40aa-3083-4613-a2c0-f1025c4b78f1", "name": "Jammu-Pacific"},
    {"id": "70341950-23ff-4d75-bda5-1707549c49bc", "name": "Mumbai Branch"},
    {"id": "447366a9-549f-4ac7-af2f-a8a1b49dc5ee", "name": "Thane Branch"}
]

# Brand new unique names for this provider
first_names = [
    "Aarav", "Advik", "Akshat", "Anay", "Ariv", "Atharva", "Ayaan", "Bhavik", "Chirag", "Darsh",
    "Devansh", "Dhruv", "Divit", "Girish", "Harsh", "Ishaan", "Jai", "Kabir", "Krish", "Lakshya",
    "Madhav", "Manan", "Mihir", "Neel", "Om", "Parth", "Pranav", "Reyansh", "Rudra", "Sai"
]

last_names = [
    "Acharya", "Bhatia", "Chawla", "D'Souza", "Fernandes", "Gill", "Hegde", "Iyer", "Jaggi", "Kohli",
    "Luthra", "Malhotra", "Nayak", "Oberoi", "Pillai", "Qureshi", "Rao", "Sethi", "Tandon", "Upadhyay",
    "Vaidya", "Wadhwa", "Xavier", "Yadav", "Zacharia", "Bhat", "Chandra", "Desai", "Gowda", "Kulkarni"
]

items = []
for i in range(30):
    # Distribute 30 students evenly across the 5 branches (6 per branch)
    branch = branches[i % 5] 
    first = random.choice(first_names)
    last = random.choice(last_names)
    
    # Generate unique IDs
    student_id = f"STU{random.randint(10000, 99999)}"
    enrollment_id = str(uuid.uuid4())
    
    # Academic info
    class_level = random.choice(["11TH", "12TH"])
    class_label = "11th" if class_level == "11TH" else "12th"
    academic_year = "2026-2027" if class_level == "11TH" else "2027-2028"
    
    items.append({
        "enrollment_id": enrollment_id,
        "provider_id": provider_id,
        "branch_id": branch["id"],
        "student_id": student_id,
        "status": "ACTIVE",
        "enrolled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "student_name": f"{first} {last}",
        "batch_ids": [],
        "batch_subjects": {},
        "academic": {
            "class_level": class_level,
            "class_label": class_label,
            "academic_year": academic_year,
            "other_class_description": None,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        }
    })

output = {
    "items": items,
    "total": len(items),
    "page": 1,
    "page_size": 30
}

# Save to a new file for this specific provider
with open("new_batch_students.json", "w") as f:
    json.dump(output, f, indent=4)

print("Generated new_batch_students.json with 30 students across 5 branches.")