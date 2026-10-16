from institute_common.appfactory import create_app

from inst_identity.routes import audit, auth, internal, staff

app = create_app("identity", "Institute Identity and Access Service", [auth.router, staff.router, audit.router],
                 internal_routers=[internal.router], ready_redis=True)
