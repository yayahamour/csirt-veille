"""État de lecture fourni par le navigateur, jamais enregistré côté serveur."""
import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Sequence = Annotated[int, Field(strict=True, ge=0, le=9007199254740991)]
IncidentKey = Annotated[str, Field(pattern=r'^[1-9][0-9]{0,15}$')]


class ReadingState(BaseModel):
    model_config = ConfigDict(extra='forbid')
    generation: str = Field(min_length=1, max_length=80)
    sequence: Sequence
    seen: dict[IncidentKey, Sequence] = Field(default_factory=dict, max_length=10000)


def reading_query(reading):
    if reading is None:
        return '', 'incidents i', [], []
    # Matérialiser une fois la petite liste locale, avec jointure indexable,
    # plutôt que parcourir tout le JSON pour chaque incident.
    prefix = ('WITH reading_seen AS MATERIALIZED '
              '(SELECT CAST(key AS INTEGER) id,CAST(value AS INTEGER) sequence FROM json_each(?)) ')
    table = 'incidents i LEFT JOIN reading_seen rs ON rs.id=i.id'
    clauses = ['i.activity_seq>?', 'i.activity_seq>COALESCE(rs.sequence,0)',
               '(SELECT generation FROM alert_state WHERE id=1)=?']
    params = [json.dumps(reading.seen, separators=(',', ':')), reading.sequence, reading.generation]
    return prefix, table, clauses, params
