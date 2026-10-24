#!/usr/bin/env python3
"""Train a compact contrastive candidate-ranker baseline from annotations.

This is an invoice-specific semantic head, not a claim that the upstream CLM-8B weights were fine-tuned.
It can be replaced by the official CLM scoring endpoint after model/runtime and license verification.
"""
import argparse,json,hashlib,random,sys
from pathlib import Path
import torch
from torch import nn
from torch.nn import functional as F
from checkpointing import Budget,load_checkpoint,save_checkpoint

def vec(text,dim=128):
 x=torch.zeros(dim)
 for i in range(max(1,len(text)-2)):
  token=text[i:i+3].casefold().encode();x[int.from_bytes(hashlib.blake2b(token,digest_size=4).digest(),'little')%dim]+=1
 return F.normalize(x,p=2,dim=0)
def data(path,dim):
 names=json.load(open(Path(__file__).resolve().parents[1]/'configs/field_ontology.json'))
 pos=[]
 for field,spec in names.items():
  for alias in [field,*spec['aliases'],*spec.get('abbreviations',[])]:pos.append((vec(alias,dim),vec(field,dim),1.0))
 neg=[]
 fields=list(names)
 for a,b,_ in pos:
  # Deterministic negative field embeddings keep ambiguous entity/charge labels in training.
  for name in fields: neg.append((a,vec(name,dim),float((a-vec(name,dim)).square().sum()>.3)))
 random.Random(91731).shuffle(neg); rows=pos+neg[:len(pos)*4]
 return rows
class Ranker(nn.Module):
 def __init__(self,d):super().__init__();self.left=nn.Linear(d,d);self.right=nn.Linear(d,d)
 def forward(self,a,b):return F.cosine_similarity(self.left(a),self.right(b),dim=-1)
def main():
 p=argparse.ArgumentParser();p.add_argument('--config',default='configs/clm.yaml');p.add_argument('--resume');p.add_argument('--output',default='checkpoints/clm');a=p.parse_args();cfg=json.load(open(a.config)) if a.config.endswith('.json') else __import__('yaml').safe_load(open(a.config));
 torch.manual_seed(cfg['seed']);device='cuda' if torch.cuda.is_available() else ('mps' if torch.backends.mps.is_available() else 'cpu');d=cfg['embedding_dim'];rows=data('',d);random.Random(cfg['seed']).shuffle(rows);cut=max(1,int(.8*len(rows)));train,valid=rows[:cut],rows[cut:];model=Ranker(d).to(device);opt=torch.optim.AdamW(model.parameters(),lr=cfg['learning_rate']);start_epoch=start_step=0
 if a.resume:
  z=load_checkpoint(a.resume,model=model,optimizer=opt,map_location=device);start_epoch=z['epoch'];start_step=z['step']
 budget=Budget(min(85,cfg.get('max_minutes',85)));step=start_step
 for epoch in range(start_epoch,cfg['epochs']):
  random.shuffle(train);model.train()
  for i in range(0,len(train),cfg['batch_size']):
   batch=train[i:i+cfg['batch_size']];x=torch.stack([r[0] for r in batch]).to(device);y=torch.stack([r[1] for r in batch]).to(device);target=torch.tensor([r[2]*2-1 for r in batch],device=device);loss=F.mse_loss(model(x,y),target);opt.zero_grad();loss.backward();opt.step();step+=1
   if step%cfg['checkpoint_every_steps']==0:save_checkpoint(Path(a.output)/'latest.pt',model=model,optimizer=opt,epoch=epoch,step=step,config=cfg,dataset_version='ontology-v1')
   if budget.expired():save_checkpoint(Path(a.output)/'latest.pt',model=model,optimizer=opt,epoch=epoch,step=step,config=cfg,dataset_version='ontology-v1');print(f'budget stop epoch={epoch} step={step}; resume with --resume {a.output}/latest.pt');return
  model.eval();v=valid;xx=torch.stack([r[0] for r in v]).to(device);yy=torch.stack([r[1] for r in v]).to(device);tt=torch.tensor([r[2]*2-1 for r in v],device=device);vl=float(F.mse_loss(model(xx,yy),tt));save_checkpoint(Path(a.output)/'latest.pt',model=model,optimizer=opt,epoch=epoch+1,step=step,config=cfg,dataset_version='ontology-v1',best_metric=vl);print(f'epoch={epoch+1} validation_mse={vl:.4f} device={device}')
  if not (Path(a.output)/'best.pt').exists() or vl<float(torch.load(Path(a.output)/'best.pt',map_location='cpu',weights_only=False).get('best_metric',1e9)):save_checkpoint(Path(a.output)/'best.pt',model=model,optimizer=opt,epoch=epoch+1,step=step,config=cfg,dataset_version='ontology-v1',best_metric=vl)
if __name__=='__main__':main()
