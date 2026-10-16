from institute_common.appfactory import create_app

from inst_enrollment.routes import enrollments, internal
from inst_enrollment.services import reference_sync

app = create_app("enrollment", "Institute Enrollment Service", [enrollments.router], internal_routers=[internal.router],
                 on_startup=[reference_sync.start_background])
