# from alembic import op

# revision = '0002_add_batch_status_enum'
# down_revision = '0001_organization_baseline'
# branch_labels = None
# depends_on = None

# def upgrade():
#     op.execute("CREATE TYPE batch_status AS ENUM ('ACTIVE', 'INACTIVE')")

# def downgrade():
#     op.execute("DROP TYPE batch_status")