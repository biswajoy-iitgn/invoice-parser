#!/usr/bin/env python3
"""Check corpus cardinality, split counts, duplicate bytes and template/vendor leakage."""
import argparse,json,collections

def main():
 p=argparse.ArgumentParser();p.add_argument('manifest',nargs='?',default='dataset/manifests/all.jsonl');p.add_argument('--expected',type=int,default=5000);a=p.parse_args();rows=[json.loads(x) for x in open(a.manifest) if x.strip()];splits=collections.Counter(r['split'] for r in rows);errors=[]
 if len(rows)!=a.expected:errors.append(f"expected {a.expected} records, got {len(rows)}")
 expected={'train':int(a.expected*.6),'val':int(a.expected*.2),'test':a.expected-int(a.expected*.6)-int(a.expected*.2)}
 if splits!=expected:errors.append(f"split mismatch: expected {expected}, got {dict(splits)}")
 for field in ('sha256','template_id','vendor_id','layout_family'):
  groups=collections.defaultdict(set)
  for r in rows:
   if r.get(field):groups[r[field]].add(r['split'])
  leak=[k for k,v in groups.items() if len(v)>1]
  if leak:errors.append(f"{field} leakage: {len(leak)} values cross splits")
 print(json.dumps({'documents':len(rows),'splits':dict(splits),'unique_ids':len({r['id'] for r in rows}),'unique_image_hashes':len({r.get('sha256') for r in rows if r.get('sha256')}),'template_count':len({r.get('template_id') for r in rows}),'vendor_count':len({r.get('vendor_id') for r in rows}),'errors':errors},indent=2));raise SystemExit(bool(errors))
if __name__=='__main__':main()
