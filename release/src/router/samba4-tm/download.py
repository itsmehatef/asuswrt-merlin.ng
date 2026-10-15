#!/usr/bin/env python3
import concurrent.futures, hashlib, json, os, pathlib, urllib.request
root = pathlib.Path(os.environ.get('TM_ROOT','/build/tm-samba'))/'downloads'
root.mkdir(parents=True, exist_ok=True)
items = [
 ('samba-4.24.8.tar.gz','https://download.samba.org/pub/samba/stable/samba-4.24.8.tar.gz','9b6611ab9ae718f47ef0d54008403d7b60a3c6c13e59b2aba769b085caea1d75'),
 ('gmp-6.3.0.tar.gz','https://ftp.gnu.org/gnu/gmp/gmp-6.3.0.tar.gz','e56fd59d76810932a0555aa15a14b61c16bed66110d3c75cc2ac49ddaa9ab24c'),
 ('nettle-3.10.2.tar.gz','https://ftp.gnu.org/gnu/nettle/nettle-3.10.2.tar.gz','fe9ff51cb1f2abb5e65a6b8c10a92da0ab5ab6eaf26e7fc2b675c45f1fb519b5'),
 ('gnutls-3.8.13.tar.xz','https://www.gnupg.org/ftp/gcrypt/gnutls/v3.8/gnutls-3.8.13.tar.xz','ffed8ec1bf09c2426d4f14aae377de4753b53e537d685e604e99a8b16ca9c97e'),
 ('libtasn1-4.21.0.tar.gz','https://ftp.gnu.org/gnu/libtasn1/libtasn1-4.21.0.tar.gz','1d8a444a223cc5464240777346e125de51d8e6abf0b8bac742ac84609167dc87'),
 ('popt-1.19.tar.gz','https://ftp.osuosl.org/pub/rpm/popt/releases/popt-1.x/popt-1.19.tar.gz','c25a4838fc8e4c1c8aacb8bd620edb3084a3d63bf8987fdad3ca2758c63240f9'),
 ('zlib-1.3.2.tar.xz','https://zlib.net/zlib-1.3.2.tar.xz','d7a0654783a4da529d1bb793b7ad9c3318020af77667bcae35f95d0e42a792f3'),
 ('attr-2.6.0.tar.xz','https://download.savannah.nongnu.org/releases/attr/attr-2.6.0.tar.xz','6c8a2148a7b85043b68492bce43316b0e2e214fc4e628c7ede078e76e216330b'),
]
def get(item):
 name,url,expected=item
 path=root/name
 if not path.exists():
  with urllib.request.urlopen(url,timeout=120) as source: path.write_bytes(source.read())
 actual=hashlib.sha256(path.read_bytes()).hexdigest()
 if expected and expected!=actual: raise ValueError(f'hash mismatch: {name}: {actual}')
 print(name,actual,flush=True)
 return {'file':name,'url':url,'sha256':actual,'expected_sha256':expected}
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
 results=list(pool.map(get,items))
(root/'source-manifest.json').write_text(json.dumps(results,indent=2)+'\n')
