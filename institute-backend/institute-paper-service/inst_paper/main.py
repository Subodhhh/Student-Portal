from institute_common.appfactory import create_app

from inst_paper.routes import bulk_imports, internal, papers, review_drafts, taxonomy

app = create_app("paper", "Institute Question and Paper Service", [papers.router, review_drafts.router, taxonomy.router, bulk_imports.router], internal_routers=[internal.router])
