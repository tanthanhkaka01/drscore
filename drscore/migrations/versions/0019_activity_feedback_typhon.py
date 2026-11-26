"""activity log, encrypted secrets, feedback (Góp ý), the AI agent Typhon, Telegram / Zalo notices

Owner, 2026-11-09 (ORD 440): as petweb does - a full log of every user action; a feedback section
any signed-in user writes in and administrators handle; an AI agent named Typhon; notices to a
Telegram group and a Zalo group, each off until its group and bot key are set. The AI key and the
bot tokens are kept encrypted with DRS_SECRET_KEY (drs_secret), never in a file in clear.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-09
"""

from alembic import op
import sqlalchemy as sa

import drscore.db.types

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None

ID = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')
JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), 'postgresql')
NOW = sa.text('(CURRENT_TIMESTAMP)')


def _time(name, nullable=False, now=True):
    return sa.Column(name, drscore.db.types.UTCDateTime(timezone=True),
                     server_default=NOW if now else None, nullable=nullable)


def upgrade() -> None:
    op.create_table('drs_activity_log',
    sa.Column('activity_id', ID, autoincrement=True, nullable=False),
    _time('logged_at'),
    sa.Column('username', sa.String(length=100), nullable=False),
    sa.Column('action', sa.String(length=60), nullable=False),
    sa.Column('object_key', sa.String(length=200), nullable=True),
    sa.Column('method', sa.String(length=10), nullable=False),
    sa.Column('path', sa.String(length=400), nullable=False),
    sa.Column('query_text', sa.String(length=2000), nullable=True),
    sa.Column('status_code', sa.Integer(), nullable=False),
    sa.Column('outcome', sa.String(length=10), nullable=False),
    sa.Column('detail_json', JSON, nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('client_ip', sa.String(length=64), nullable=True),
    sa.Column('user_agent', sa.String(length=400), nullable=True),
    sa.Column('request_id', sa.String(length=32), nullable=True),
    sa.CheckConstraint("outcome IN ('OK', 'DENIED', 'ERROR')", name=op.f('ck_drs_activity_log_outcome')),
    sa.PrimaryKeyConstraint('activity_id', name=op.f('pk_drs_activity_log')),
    sqlite_autoincrement=True
    )
    op.create_index(op.f('ix_drs_activity_log_logged_at'), 'drs_activity_log', ['logged_at'])
    op.create_index(op.f('ix_drs_activity_log_username_logged_at'), 'drs_activity_log', ['username', 'logged_at'])

    op.create_table('drs_secret',
    sa.Column('secret_name', sa.String(length=60), nullable=False),
    sa.Column('secret_ref', sa.Text(), nullable=False),
    sa.Column('updated_by', sa.String(length=100), nullable=False),
    _time('updated_at'),
    sa.PrimaryKeyConstraint('secret_name', name=op.f('pk_drs_secret'))
    )

    op.create_table('drs_feedback',
    sa.Column('feedback_id', ID, autoincrement=True, nullable=False),
    sa.Column('username', sa.String(length=100), nullable=False),
    sa.Column('kind', sa.String(length=10), server_default='bug', nullable=False),
    sa.Column('urgency', sa.String(length=10), server_default='normal', nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('page_path', sa.String(length=400), nullable=True),
    sa.Column('app_version', sa.String(length=20), nullable=True),
    sa.Column('status', sa.String(length=10), server_default='new', nullable=False),
    sa.Column('done_version', sa.String(length=20), nullable=True),
    sa.Column('ai_note', sa.Text(), nullable=True),
    sa.Column('handled_by', sa.String(length=100), nullable=True),
    _time('handled_at', nullable=True, now=False),
    _time('checked_at', nullable=True, now=False),
    _time('created_at'),
    _time('updated_at'),
    sa.CheckConstraint("kind IN ('bug', 'idea', 'question')", name=op.f('ck_drs_feedback_kind')),
    sa.CheckConstraint("urgency IN ('high', 'normal', 'low')", name=op.f('ck_drs_feedback_urgency')),
    sa.CheckConstraint("status IN ('new', 'seen', 'doing', 'done', 'wontfix')", name=op.f('ck_drs_feedback_status')),
    sa.PrimaryKeyConstraint('feedback_id', name=op.f('pk_drs_feedback')),
    sqlite_autoincrement=True
    )
    op.create_index(op.f('ix_drs_feedback_username'), 'drs_feedback', ['username'])
    op.create_index(op.f('ix_drs_feedback_status'), 'drs_feedback', ['status'])

    op.create_table('drs_feedback_comment',
    sa.Column('comment_id', ID, autoincrement=True, nullable=False),
    sa.Column('feedback_id', ID, nullable=False),
    sa.Column('username', sa.String(length=100), nullable=False),
    sa.Column('author_kind', sa.String(length=10), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    _time('created_at'),
    sa.CheckConstraint("author_kind IN ('user', 'admin', 'ai')", name=op.f('ck_drs_feedback_comment_author_kind')),
    sa.ForeignKeyConstraint(['feedback_id'], ['drs_feedback.feedback_id'],
                            name=op.f('fk_drs_feedback_comment_feedback_id_drs_feedback'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('comment_id', name=op.f('pk_drs_feedback_comment')),
    sqlite_autoincrement=True
    )

    op.create_table('drs_feedback_file',
    sa.Column('file_id', ID, autoincrement=True, nullable=False),
    sa.Column('feedback_id', ID, nullable=False),
    sa.Column('file_name', sa.String(length=200), nullable=False),
    sa.Column('media_type', sa.String(length=100), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('stored_name', sa.String(length=100), nullable=False),
    _time('created_at'),
    sa.ForeignKeyConstraint(['feedback_id'], ['drs_feedback.feedback_id'],
                            name=op.f('fk_drs_feedback_file_feedback_id_drs_feedback'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('file_id', name=op.f('pk_drs_feedback_file')),
    sqlite_autoincrement=True
    )

    op.create_table('drs_ai_conversation',
    sa.Column('conversation_id', ID, autoincrement=True, nullable=False),
    sa.Column('username', sa.String(length=100), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('messages_json', JSON, nullable=False),
    _time('created_at'),
    _time('updated_at'),
    sa.PrimaryKeyConstraint('conversation_id', name=op.f('pk_drs_ai_conversation')),
    sqlite_autoincrement=True
    )
    op.create_index(op.f('ix_drs_ai_conversation_username'), 'drs_ai_conversation', ['username'])

    op.create_table('drs_ai_usage',
    sa.Column('usage_id', ID, autoincrement=True, nullable=False),
    _time('asked_at'),
    sa.Column('username', sa.String(length=100), nullable=False),
    sa.Column('purpose', sa.String(length=20), server_default='ask', nullable=False),
    sa.Column('conversation_id', ID, nullable=True),
    sa.Column('feedback_id', ID, nullable=True),
    sa.Column('page_path', sa.String(length=400), nullable=True),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('answer', sa.Text(), nullable=True),
    sa.Column('tools_json', JSON, nullable=True),
    sa.Column('model', sa.String(length=60), nullable=True),
    sa.Column('input_tokens', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('output_tokens', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('cache_read_tokens', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('cache_write_tokens', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('cost_usd', sa.Numeric(precision=12, scale=6), server_default=sa.text('0'), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('error_message', sa.String(length=2000), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.CheckConstraint("status IN ('ok', 'refused', 'error')", name=op.f('ck_drs_ai_usage_status')),
    sa.PrimaryKeyConstraint('usage_id', name=op.f('pk_drs_ai_usage')),
    sqlite_autoincrement=True
    )
    op.create_index(op.f('ix_drs_ai_usage_asked_at'), 'drs_ai_usage', ['asked_at'])
    op.create_index(op.f('ix_drs_ai_usage_username_asked_at'), 'drs_ai_usage', ['username', 'asked_at'])

    op.create_table('drs_notice_log',
    sa.Column('notice_id', ID, autoincrement=True, nullable=False),
    _time('sent_at'),
    sa.Column('channel', sa.String(length=10), nullable=False),
    sa.Column('chat_id', sa.String(length=100), nullable=False),
    sa.Column('event', sa.String(length=40), nullable=False),
    sa.Column('text_sent', sa.Text(), nullable=False),
    sa.Column('is_ok', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('error_message', sa.String(length=1000), nullable=True),
    sa.PrimaryKeyConstraint('notice_id', name=op.f('pk_drs_notice_log')),
    sqlite_autoincrement=True
    )
    op.create_index(op.f('ix_drs_notice_log_sent_at'), 'drs_notice_log', ['sent_at'])


def downgrade() -> None:
    op.drop_table('drs_notice_log')
    op.drop_table('drs_ai_usage')
    op.drop_table('drs_ai_conversation')
    op.drop_table('drs_feedback_file')
    op.drop_table('drs_feedback_comment')
    op.drop_table('drs_feedback')
    op.drop_table('drs_secret')
    op.drop_table('drs_activity_log')
