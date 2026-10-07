"""Vettori del vero protocollo Python, senza dati o chiavi dell'utente."""
import base64
import json
from pathlib import Path
import sys
from aios_copilot import crypto, ed25519
from aios_copilot.identity import canonical
from aios_copilot.sync import SyncEngine

path = Path(sys.argv[2])
key, seed = bytes(range(32)), bytes(range(32, 64))
def b64(value):
    return base64.b64encode(value).decode()

if sys.argv[1] == 'prepare':
    plain, aad, info = 'Un caffè, grazie ☕'.encode(), b'header', b'1111111111111111|2'
    wrapped = crypto.wrap_for(ed25519.to_x25519_public(ed25519.public_key(seed)), key, info)
    engine = SyncEngine(key, '2222222222222222', [], db_path=path.parent / 'pc.db', wall=lambda: 99)
    engine._local('note/pc', 'Caffè sul PC')
    doc = {'z': 'caffè ☕\n"', 'a': [1, True, None, {'id': 7}]}
    path.write_text(json.dumps(dict(seed=b64(seed), key=b64(key), public=b64(ed25519.public_key(seed)),
        signature=b64(ed25519.sign(seed, plain)), plain=b64(plain), aad=b64(aad), box=b64(crypto.seal(key, plain, aad)),
        eph=wrapped['eph'], wrapped=wrapped['box'], info=b64(info), canonical_doc=doc,
        canonical=b64(canonical(doc)), ops=engine.ops_since(0)[0])))
else:
    engine = SyncEngine(key, '2222222222222222', [], db_path=path / 'return.db')
    engine.merge(json.loads((path / 'android-ops.json').read_text()))
    engine.scan()
    row = engine.db.execute('SELECT value FROM snapshot WHERE key=?', ('note/android',)).fetchone()
    assert row and json.loads(row[0]) == 'Caffè sul telefono'
    print('PASS telefono → PC: crittografia compatibile e note conservate dopo scan')
