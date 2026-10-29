from institute_common.appfactory import create_app

from inst_exam.routes import composite, corrections, exams, global_contributions, global_schedule, global_tests, integrity, test_series, tests

app = create_app("exam", "Institute Exam Management Service", [exams.router, corrections.router, composite.router, global_tests.router,
                                                               global_contributions.router, global_schedule.router, tests.router, test_series.router,
                                                               integrity.router])
