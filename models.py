import sqlalchemy


metadata = sqlalchemy.MetaData()

stickers = sqlalchemy.Table(
    'stickers',
    metadata,
    sqlalchemy.Column('id', sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column('user_id', sqlalchemy.String(length=16)),
    sqlalchemy.Column('message_id', sqlalchemy.Integer()),
    sqlalchemy.Column('file_unique_id', sqlalchemy.String(length=16)),
    sqlalchemy.Column('tag', sqlalchemy.String(length=16)),
    sqlalchemy.UniqueConstraint('user_id', 'file_unique_id', name='user_file_uc'),
)
