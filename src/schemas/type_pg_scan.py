from jsl import Document
from jsl.fields import ArrayField, DictField, DocumentField, StringField, IntField
from snactor.registry.schemas import registered_schema


class PGElement(Document):
    config = StringField()
    properties = ArrayField(items=DictField())


class PGInfo(Document):
    hba = DocumentField(PGElement())
    pg = DocumentField(PGElement())
    pg_data_size = IntField(minimum=0)


@registered_schema('1.0')
class TypePGScan(Document):
    pg_scan_info = DictField(properties=DocumentField(PGInfo()))
