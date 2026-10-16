from institute_common.appfactory import create_app

from inst_exam.routes import composite, corrections, exams

app = create_app("exam", "Institute Exam Management Service", [exams.router, corrections.router, composite.router])
