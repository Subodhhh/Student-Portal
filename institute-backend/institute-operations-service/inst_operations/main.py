from institute_common.appfactory import create_app

from inst_operations.routes import internal, ops

app = create_app("operations", "Institute Operations Service", [ops.router], internal_routers=[internal.router])
