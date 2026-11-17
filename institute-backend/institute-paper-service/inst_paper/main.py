from institute_common.appfactory import create_app

from inst_paper.routes import internal, papers, review_drafts

app = create_app("paper", "Institute Question and Paper Service", [papers.router, review_drafts.router], internal_routers=[internal.router])
