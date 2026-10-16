from institute_common.appfactory import create_app

from inst_academics.routes import academics, internal, teaching

app = create_app("academics", "Institute Academics Service", [academics.router, teaching.router], internal_routers=[internal.router])
