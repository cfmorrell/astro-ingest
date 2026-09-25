import sys, os, re, glob, json
def header(path):
    h={}
    if path.lower().endswith('.xisf'):
        try:
            b=open(path,'rb').read(200000).decode('utf-8','replace')
            for m in re.finditer(r'<FITSKeyword name="([^"]+)" value="([^"]*)"',b):
                k=m.group(1); v=m.group(2).strip().strip("'").strip()
                if k not in h: h[k]=v
        except Exception as e: h['_error']=str(e)
        return h
    try:
        with open(path,'rb') as f:
            while True:
                blk=f.read(2880)
                if len(blk)<2880: break
                for i in range(0,2880,80):
                    card=blk[i:i+80].decode('ascii','replace')
                    k=card[:8].strip()
                    if k=='END': return h
                    if card[8:10]=='= ':
                        v=card[10:]
                        if v.strip().startswith("'"):
                            m=re.match(r"\s*'((?:[^']|'')*)'",v); val=m.group(1).replace("''","'").strip() if m else v.strip()
                        else:
                            val=v.split('/')[0].strip()
                        if k not in h: h[k]=val
    except Exception as e:
        h['_error']=str(e)
    return h
if __name__=='__main__':
    for p in sys.argv[1:]:
        print(json.dumps(header(p)))
