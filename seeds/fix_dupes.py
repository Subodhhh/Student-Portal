import os
from sqlalchemy import create_engine, text

engine = create_engine(os.environ.get("DATABASE_URL"))

duplicates_to_remove = [
    # Andheri
    "STU000754", "STU000749", "STU000739",
    
    # Bhawarkuan
    "STU000740", "STU000735",
    
    # Janakpuri
    "STU000756", "STU000751", "STU000746", 
    "STU000741", "STU000736", "STU000731",
    
    # Kota
    "STU000757", "STU000752", "STU000747", 
    "STU000742", "STU000737", "STU000732",
    
    # NewTown
    "STU000758", "STU000753", "STU000748", 
    "STU000743", "STU000738", "STU000733"
]

with engine.connect() as conn:
    for stu_id in duplicates_to_remove:
        result = conn.execute(
            text("DELETE FROM enrollments WHERE student_id = :sid"),
            {"sid": stu_id}
        )
        print(f"Deleted enrollment for {stu_id}: {result.rowcount} rows")
        
    conn.commit()
    print("Cleanup complete! Refresh your browser.")