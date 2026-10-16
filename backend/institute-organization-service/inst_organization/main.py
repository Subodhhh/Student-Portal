from institute_common.appfactory import create_app

from inst_organization.routes import orgs

app = create_app("organization", "Institute Organization Service", [orgs.router])
