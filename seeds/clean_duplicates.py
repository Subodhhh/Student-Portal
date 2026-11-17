import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

def clean_duplicates():
    print("Searching for duplicate students in the same branch...")
    
    # Join enrollments with students to compare first_name and last_name
    delete_query = text("""
        DELETE FROM enrollments e1
        USING enrollments e2, students s1, students s2
        WHERE e1.branch_id = e2.branch_id 
          AND e1.student_id = s1.student_id
          AND e2.student_id = s2.student_id
          AND s1.first_name = s2.first_name
          AND s1.last_name = s2.last_name
          AND e1.enrolled_at > e2.enrolled_at;
    """)

    try:
        result = db.execute(delete_query)
        db.commit()
        print(f"✅ Successfully deleted {result.rowcount} duplicate enrollment records!")
    except Exception as e:
        db.rollback()
        print(f"❌ Error deleting duplicates: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    clean_duplicates()