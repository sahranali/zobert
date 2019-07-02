import argparse
import logging
from dataloader import DataLoader
from LanguageModel import LanguageModel
import torch.optim as optim
import torch.nn as nn
import torch
import time
from trainutils import Timer, Average, ValueHistory

parser = argparse.ArgumentParser()
parser.add_argument('--input_h5', default='data/tiny-shakespeare.h5')
parser.add_argument('--input_json', default='data/tiny-shakespeare.json')
parser.add_argument('--batch_size', default=64, type=int)
parser.add_argument('--seq_length', default=64, type=int)

parser.add_argument('--num_epochs', default=50, type=int)

parser.add_argument('--num_layers', default=2, type=int)
parser.add_argument('--embedding_dim', default=128, type=int)
parser.add_argument('--hidden_dim', default=128, type=int)
parser.add_argument('--zoneout', default=0, type=float)
parser.add_argument('--dropout', default=0, type=float)

parser.add_argument('--learning-rate', default=0.002, type=float)
parser.add_argument('--lrdecay-every', default=5, type=int)
parser.add_argument('--lrdecay-factor', default=0.5, type=float)
parser.add_argument('--checkpoint', default='models/output')

parser.add_argument('--device', default='cpu')
parser.add_argument('--print-every', default=1, type=float)
args = parser.parse_args()

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger('train')

logger.info('Creating model')
model = LanguageModel()
model.load_tokendata(args.input_json)
model.build_model(
  layertype = 'GRIDGRU',
  dropout = args.dropout,
  num_layers = args.num_layers,
  D = args.embedding_dim,
  H = args.hidden_dim,
  zoneout = args.zoneout
  )
print(model.layers)
logger.info('Created model with %d parameters' % sum((p.numel() for p in model.parameters())))
optimizer = optim.Adam(model.parameters(), lr=args.learning_rate)
scheduler = optim.lr_scheduler.StepLR(optimizer, args.lrdecay_every, args.lrdecay_factor)
crit = nn.CrossEntropyLoss()

logger.info('Loading data')

loader = DataLoader(
  filename = args.input_h5,
  batch_size = args.batch_size,
  seq_length = args.seq_length
  )

device = torch.device(args.device)
model.to(device)

totalfwd = 0
totalbck = 0
timer_pre = Timer()
timer_fwd = Timer()
timer_bck = Timer()
timer_tot = Timer()
avg_tloss = Average(100)
vloss_history = ValueHistory('val loss')

for epoch in range(0, args.num_epochs):
  traindata = loader.make_batches('train', 0)
  timer_pre.reset()
  timer_fwd.reset()
  timer_bck.reset()
  timer_tot.reset()
  totalloss = 0
  model.train()
  for iter_data in traindata.data:
    timer_tot.start()
    N = iter_data.inputs.size(0)
    T = iter_data.inputs.size(1)
    optimizer.zero_grad()
    model.clear_states()
    with torch.no_grad(), timer_pre:
      model(iter_data.preinputs.to(device).long())
    with timer_fwd:
      outputs = model(iter_data.inputs.to(device).long())
      loss = crit(outputs.view(N*T, -1), iter_data.outputs.to(device).long().view(N*T))
    with timer_bck:
      loss.backward()
    optimizer.step()
    timer_tot.stop()
    totalloss += loss.detach()
    avg_tloss.add_value(loss.detach())
    if iter_data.i % args.print_every == 0:
      print('ep %d/%d iter %d/%d loss=%.4f, %.4f lr=%.2e Times: %.2f %.2f %.2f %.2f (%4.1f tps)' %
        (epoch, args.num_epochs, iter_data.i, traindata.batch_count, loss, avg_tloss.avg(), optimizer.param_groups[0]['lr'], timer_pre.last, timer_fwd.last, timer_bck.last, timer_tot.last, N*T/timer_tot.average()))
  print('average loss: %.4f' % (totalloss.item()/traindata.batch_count))

  model.clear_states()
  model.eval()
  valdata = loader.make_batches('val', shuffle=False)
  timer_tot.reset()
  timer_fwd.reset()
  with torch.no_grad():
    totalloss = torch.Tensor([0])
    for iter_data in valdata.data:
      timer_tot.start()
      if iter_data.preinputs is not None:
        model(iter_data.preinputs.to(device).long())
      with timer_fwd:
        outputs = model(iter_data.inputs.to(device).long())
      loss = crit(outputs.view(N*T, -1), iter_data.outputs.to(device).long().view(N*T))
      totalloss += loss
      timer_tot.stop()
      if iter_data.i % args.print_every == 0:
        print('ep %d/%d iter %d/%d loss: %.4f Time: %.2f %.2f (%4.1f tps)' % (epoch, args.num_epochs, iter_data.i, traindata.batch_count, loss, timer_fwd.last, timer_tot.last, (iter_data.inputs.size(0)*iter_data.inputs.size(1))/timer_tot.last))
    vloss_history.add_value(epoch, totalloss.item()/valdata.batch_count)
    print(vloss_history.format())
  scheduler.step()
