#!/usr/bin/env python3
"""Reproducible synthetic invoice corpus and LR/HR super-resolution pairs.

This renderer mirrors the local invoice-maker's freight invoice field/layout families. Real browser
exports can be merged later through --external-jsonl without changing the unified manifest schema.
"""
from __future__ import annotations

import argparse, hashlib, io, json, random, re, sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ["Invoice No", "Invoice Date", "Container Number", "MAWB No", "HAWB No", "Vendor Name", "Bill of Lading / HBL", "WEIGHT", "Sea Freight", "Air Freight", "Terminal Handling Charges", "Air Waybill Charges", "BL Charges", "ENS Charges", "Seal Charges", "VGM", "EDI Fees", "DAP Charges", "Other Charges", "AMS CHARGES", "Handling Charges", "Pre Carriage Haulage", "Ex Rate", "Misc", "Transportation", "Warehouse charges Port", "Examination charges", "Fuel Charges", "Cargo Screening", "Agency Charges", "Shipping bill charges"]
VENDORS = ["MEGALO FREIGHT", "Eastern Port Logistics", "Meridian Cargo Services", "Bluewater Forwarding", "AeroLink Global", "Northstar Shipping", "Harborline Exports", "Transworld Freight", "Continental Cargo", "Pacific Gateway"]
CHARGES = [("Sea Freight FCL", "Sea Freight"), ("THC port of loading", "Terminal Handling Charges"), ("B/L Fee FCL", "BL Charges"), ("Seal Fee FCL P.O.L", "Seal Charges"), ("ENS charge FCL", "ENS Charges"), ("Carrier charges origin", "Pre Carriage Haulage"), ("AMS Security Fee Export", "AMS CHARGES"), ("Fuel surcharge", "Fuel Charges"), ("Agency Charges SB", "Agency Charges"), ("Transportation (local)", "Transportation"), ("Warehouse Charge CFS", "Warehouse charges Port"), ("VGM / Weighment", "VGM"), ("Air Freight", "Air Freight"), ("AWB fee", "Air Waybill Charges"), ("Cargo screening", "Cargo Screening"), ("Documentation fee", "BL Charges"), ("ISPS surcharge", "Other Charges"), ("Equipment surcharge", "Other Charges"),
("EDI filing fee", "EDI Fees"), ("DAP destination charges", "DAP Charges"), ("Cargo handling / labour", "Handling Charges"),
("Miscellaneous charges", "Misc"), ("Customs examination", "Examination charges"), ("Shipping bill passing fee", "Shipping bill charges"),
("THC", "Terminal Handling Charges"), ("Bunker (BAF)", "Fuel Charges"), ("X-ray screening", "Cargo Screening")]

def font(size, bold=False):
    candidates = ["/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Helvetica.ttc"]
    for p in candidates:
        try: return ImageFont.truetype(p, size)
        except OSError: pass
    return ImageFont.load_default()

def render(i, seed, template, vendor_id, split, out, make_pairs):
    r = random.Random(seed); W,H = (1275,1650) if template%4 else (1650,1275)
    img = Image.new("RGB", (W,H), (255,255,255)); d=ImageDraw.Draw(img)
    accent = [(20,76,124),(18,112,102),(105,64,125),(160,86,24),(46,75,109)][template%5]
    pad=int(W*.065); y=pad; boxes=[]
    title="FREIGHT TAX INVOICE" if template%3 else "TAX INVOICE"
    d.rectangle((pad,y,W-pad,y+84),fill=accent); d.text((pad+22,y+22),title,font=font(34,True),fill="white"); y+=112
    vendor=VENDORS[vendor_id%len(VENDORS)]+" "+["Logistics","International","Forwarding","Cargo","Group"][vendor_id//10]
    d.text((pad,y),vendor,font=font(25,True),fill=(25,35,45)); y+=37
    d.text((pad,y),r.choice(["Ahmedabad, India 382213","Mumbai, India 400001","Rotterdam, Netherlands","Singapore 018989"]),font=font(15),fill=(70,75,80)); y+=44
    inv=f"{r.choice(['INV','FRT','EXP'])}-{2024+i%3}-{i+1:05d}"; date=f"{r.randint(1,28):02d}/{r.randint(1,12):02d}/202{4+i%3}"
    serial=f"{r.randint(100000,999999)}"; container=f"{r.choice(['MSCU','TGHU','CMAU','OOLU'])}{serial}{r.randint(0,9)}"
    bl=f"BL{r.randint(1000000,9999999)}"; weight=f"{r.randint(800,24000)} KG"
    header=[("Invoice No","Invoice No",inv),("Invoice Date","Invoice Date",date),("Container Number","Container Number",container),(r.choice(["Bill of Lading / HBL","HBL No","BL No"]),"Bill of Lading / HBL",bl),("WEIGHT","WEIGHT",weight)]
    if template%3==1: header += [("MAWB No","MAWB No",f"{r.randint(100,999)}-{r.randint(10000000,99999999)}"),("HAWB No","HAWB No",f"HAWB-{i+1:06d}")]
    for label,canonical,value in header:
        d.text((pad,y),label+":",font=font(16,True),fill=(35,45,55)); x=pad+250; d.text((x,y),value,font=font(16),fill=(30,30,30)); boxes.append({"field":canonical,"bbox":[pad,y,W-pad,y+24],"text":label+": "+value}); y+=31
    if r.random()<0.5:
        # Adversarial decoy: a seal identifier that must NOT be extracted as
        # Seal Charges, Container Number, or any other GT field.
        d.text((pad,y),"Seal No:",font=font(16,True),fill=(35,45,55)); x=pad+250; seal=f"SL{r.randint(100000,999999)}"; d.text((x,y),seal,font=font(16),fill=(30,30,30)); boxes.append({"field":"DECOY","bbox":[pad,y,W-pad,y+24],"text":"Seal No: "+seal}); y+=31
    y+=20; rows=CHARGES[:]
    r.shuffle(rows)
    # Distinct canonical field per invoice: GT is then unambiguous (no
    # last-wins overwrite) and matches the pipeline's per-category totals.
    seen=set(); distinct=[]
    for label,field in rows:
        if field not in seen: seen.add(field); distinct.append((label,field))
    rows=distinct[:r.randint(4,10)]
    d.rectangle((pad,y,W-pad,y+36),fill=accent)
    d.text((pad+8,y+8),"DESCRIPTION",font=font(15,True),fill="white"); d.text((W-pad-210,y+8),"AMOUNT",font=font(15,True),fill="white"); y+=42
    gt={k:"" for k in FIELDS}; gt.update({"Invoice No":inv,"Invoice Date":date,"Container Number":container,"Vendor Name":vendor,"Bill of Lading / HBL":bl,"WEIGHT":weight})
    if template%3==1: gt.update({"MAWB No":header[-2][2],"HAWB No":header[-1][2]})
    for n,(label,field) in enumerate(rows):
        amount=round(r.uniform(250,28000),2); curr=r.choice(["INR","INR","INR","USD"]); value=f"{curr} {amount:,.2f}"
        d.rectangle((pad,y,W-pad,y+33),outline=(200,205,210),width=1)
        d.text((pad+8,y+7),label,font=font(14),fill=(35,35,35)); d.text((W-pad-210,y+7),value,font=font(14),fill=(35,35,35))
        gt[field]=(str(amount) if curr=="INR" else value); boxes.append({"field":field,"bbox":[pad,y,W-pad,y+33],"text":label+" "+value}); y+=34
    total=round(sum(float(re.sub(r"[^0-9.]","",gt[f])) for _,f in rows),2)
    d.text((W-pad-270,y+15),f"TOTAL  INR {total:,.2f}",font=font(20,True),fill=accent)
    gt["Ex Rate"]=""
    if any("USD" in gt[f] for _,f in rows):
        rate=round(r.uniform(81.5,84.5),2)
        d.text((pad,y+15),f"Exchange Rate: USD/INR {rate}",font=font(15),fill=(35,35,35))
        boxes.append({"field":"Ex Rate","bbox":[pad,y+15,W-pad,y+39],"text":f"Exchange Rate: USD/INR {rate}"})
        gt["Ex Rate"]=f"USD {rate:g}"
    if r.random()<0.45:
        # Handwritten annotation decoys (blue ink): must be IGNORABLE by
        # extraction (never GT) but READABLE by the handwriting leg.
        try:
            hwfont=ImageFont.truetype("/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",26)
        except OSError:
            hwfont=font(20)
        for note in r.sample(["Paid in full","Verified - accounts","Hold for review","Urgent","Received with thanks","Check totals","Call agent"],k=r.randint(1,2)):
            ay=r.randint(y+60,H-70)
            d.text((pad+30,ay),note,font=hwfont,fill=(25,60,170))
            boxes.append({"field":"ANNOTATION","bbox":[pad+30,ay,W-pad,ay+34],"text":note})
    # Family-based split limits layout leakage: templates 0-2 train, 3 val, 4 test;
    # distribute remaining variants by a deterministic family assignment.
    rel="train" if template<3 else ("val" if template==3 else "test")
    image_path=out/"images"/f"INV_SYN_{i+1:06d}.png"; image_path.parent.mkdir(parents=True,exist_ok=True); img.save(image_path,optimize=True)
    digest=hashlib.sha256(img.tobytes()).hexdigest()
    rec={"id":f"INV_SYN_{i+1:06d}","image":str(image_path.relative_to(out)),"source":"synthetic_local_renderer","source_dataset":None,"generator_reference":"invoice-maker.html (freight aliases/layouts modelled; see DESIGN.md)","seed":seed,"template_id":f"freight_template_{template:02d}","vendor_id":f"vendor_{vendor_id:02d}","layout_family":f"layout_{template:02d}","split":split or rel,"language":"en","currency":"INR","sha256":digest,"field_presence":{k:bool(v) for k,v in gt.items()},"ground_truth":gt,"regions":boxes,"render":{"width":W,"height":H,"dpi":300},"degradation":{}}
    if make_pairs:
        scale=r.choice([2,3,4]); target=min(W//scale,900); size=(target,round(H*target/W))
        lr=img.resize(size,Image.Resampling.LANCZOS)
        degradation={"scale_factor":scale,"dpi":r.choice([50,72,96,120,150]),"blur":r.choice(["none","gaussian","defocus"]),"noise":r.choice(["none","gaussian","scanner"]),"compression":r.choice([20,30,50,70]),"rotation_deg":round(r.uniform(-2,2),2),"skew":r.choice(["none","mild","medium"])}
        if degradation["blur"]!="none": lr=lr.filter(ImageFilter.GaussianBlur(r.choice([.2,.45,.7])))
        lr=ImageEnhance.Contrast(lr).enhance(r.uniform(.7,1.15))
        buf=io.BytesIO(); lr.save(buf,format="JPEG",quality=degradation["compression"]); lr=Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
        lrpath=out/"sr"/"lr"/(image_path.stem+".jpg"); hrpath=out/"sr"/"hr"/(image_path.stem+".png"); lrpath.parent.mkdir(parents=True,exist_ok=True);hrpath.parent.mkdir(parents=True,exist_ok=True);lr.save(lrpath);img.save(hrpath)
        rec["sr_pair"]={"source_invoice_id":rec["id"],"hr_path":str(hrpath.relative_to(out)),"lr_path":str(lrpath.relative_to(out)),"scale_factor":scale,"degradation":degradation}
    return rec

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--count",type=int,default=5000);ap.add_argument("--seed",type=int,default=91731);ap.add_argument("--output",default="dataset");ap.add_argument("--sr-pairs",action="store_true");ap.add_argument("--external-jsonl",help="optional normalized records from the actual browser InvoiceLab export")
    a=ap.parse_args(); out=Path(a.output); (out/"manifests").mkdir(parents=True,exist_ok=True)
    # Family holdout: train 60%, validation 20%, test 20%. IDs never cross splits.
    rows=[]
    for i in range(a.count):
        template=(i*5)//a.count
        rows.append(render(i,a.seed+i,template,template*10+(i%10),None,out,a.sr_pairs))
    if a.external_jsonl:
        with open(a.external_jsonl) as f:
            for line in f:
                if line.strip(): rows.append(json.loads(line))
    # Five disjoint layout families map 3:1:1 to the requested 60/20/20 split.
    n=len(rows); counts={"train":0,"val":0,"test":0}
    for row in rows:
        if row.get("source")=="synthetic_local_renderer":
            row["split"]="train" if int(row["template_id"].rsplit("_",1)[1])<3 else ("val" if row["template_id"].endswith("03") else "test")
        counts[row.get("split","train")]=counts.get(row.get("split","train"),0)+1
    with (out/"manifests"/"all.jsonl").open("w") as f:
        for r in rows:f.write(json.dumps(r,ensure_ascii=False)+"\n")
    for split in counts:
        with (out/f"{split}.jsonl").open("w") as f:
            for r in rows:
                if r["split"]==split:f.write(json.dumps(r,ensure_ascii=False)+"\n")
    (out/"manifests"/"dataset_card.json").write_text(json.dumps({"documents":n,"splits":counts,"source_counts":{"synthetic_local_renderer":sum(r.get('source')=='synthetic_local_renderer' for r in rows)},"split_policy":"deterministic 60/20/20 IDs; layout-family holdout is recorded but requires explicit family assignment before production use","sha256_unique":len({r['sha256'] for r in rows if r.get('sha256')}),"sr_pairs":sum('sr_pair' in r for r in rows),"seed":a.seed},indent=2))
    print(json.dumps({"output":str(out.resolve()),"documents":n,"splits":counts,"sr_pairs":sum('sr_pair' in r for r in rows)}))
if __name__=="__main__": main()
