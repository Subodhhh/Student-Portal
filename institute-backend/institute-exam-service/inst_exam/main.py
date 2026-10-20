from institute_common.appfactory import create_app

from inst_exam.routes import composite, corrections, exams, global_contributions, global_tests, test_series, tests

app = create_app("exam", "Institute Exam Management Service", [exams.router, corrections.router, composite.router, global_tests.router,
                                                               global_contributions.router, tests.router, test_series.router])
