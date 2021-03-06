from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

# Basic imports
import os,sys,time
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

# Import more libraries (after configuration is validated)
import tensorflow as tf
from fcdensenet import fcdensenet
#from uresnet import uresnet
from larcv import larcv
from larcv.dataloader2 import larcv_threadio
from config import ssnet_config

class ssnet_trainval(object):

  def __init__(self):
    self._cfg = ssnet_config()
    self._filler = None
    self._drainer = None
    self._iteration = -1

  def __del__(self):
    if self._filler:
      self._filler.reset()
    if self._drainer:
      self._drainer.finalize()

  def iteration_from_file_name(self,file_name):
    return int((file_name.split('-'))[-1])

  def override_config(self,file_name):
    self._cfg.override(file_name)
    self._cfg.dump()

  def initialize(self):
    # Instantiate and configure
    if not self._cfg.FILLER_CONFIG:
      print('Must provide larcv data filler configuration file!')
      return

    self._filler = larcv_threadio()
    filler_cfg = {'filler_name' : 'ThreadProcessor',
                  'verbosity'   : 0, 
                  'filler_cfg'  : self._cfg.FILLER_CONFIG}
    self._filler.configure(filler_cfg)
    # Start IO thread
    self._filler.start_manager(self._cfg.BATCH_SIZE)
    # If requested, construct an output stream
    if self._cfg.DRAINER_CONFIG:
      self._drainer = larcv.IOManager(self._cfg.DRAINER_CONFIG)
      self._drainer.initialize()

    # Retrieve image/label dimensions
    self._filler.next(store_entries   = (not self._cfg.TRAIN),
                      store_event_ids = (not self._cfg.TRAIN))
    dim_data = self._filler.fetch_data(self._cfg.KEYWORD_DATA).dim()
    dims = []

    self._net = fcdensenet(dims=dim_data[1:],
                           num_class = self._cfg.NUM_CLASS,
                           num_down = self._cfg.NUM_POOL,
                           num_layers = [4, 5, 7, 10, 12, 15, 12, 10, 7, 5, 4],
                           num_filters_base = self._cfg.BASE_NUM_FILTERS,
                           growth = self._cfg.GROWTH,
                           keep_prob = self._cfg.KEEP_PROB)
    '''
    self._net = uresnet(dims=dim_data[1:],
                        num_class=3, 
                        base_num_outputs=self._cfg.BASE_NUM_FILTERS, 
                        debug=False)
    '''
    if self._cfg.TRAIN:
      self._net.construct(trainable=self._cfg.TRAIN,use_weight=self._cfg.USE_WEIGHTS)
    else:
      self._net.construct(trainable=self._cfg.TRAIN,use_weight=self._cfg.USE_WEIGHTS)

    self._iteration = 0

  def run(self,sess):
    # Set random seed for reproducibility
    tf.set_random_seed(1234)
    # Configure global process (session, summary, etc.)
    # Create a bandle of summary
    merged_summary=tf.summary.merge_all()
    # Initialize variables
    sess.run(tf.global_variables_initializer())
    writer = None
    if self._cfg.LOGDIR:
      # Create a summary writer handle
      writer=tf.summary.FileWriter(self._cfg.LOGDIR)
      writer.add_graph(sess.graph)
    saver = None
    if self._cfg.SAVE_FILE:
      # Create weights saver
      saver = tf.train.Saver()
      
    # Override variables if wished
    if self._cfg.LOAD_FILE:
      vlist=[]
      self._iteration = self.iteration_from_file_name(self._cfg.LOAD_FILE)
      parent_vlist = []
      if self._cfg.TRAIN: 
        parent_vlist = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES)
      else:
        parent_vlist = tf.get_collection(tf.GraphKeys.MODEL_VARIABLES)
      for v in parent_vlist:
        if v.name in self._cfg.AVOID_LOAD_PARAMS:
          print('\033[91mSkipping\033[00m loading variable',v.name,'from input weight...')
          continue
        print('\033[95mLoading\033[00m variable',v.name,'from',self._cfg.LOAD_FILE)
        vlist.append(v)
      reader=tf.train.Saver(var_list=vlist)
      reader.restore(sess,self._cfg.LOAD_FILE)
    
    # Run iterations
    for i in xrange(self._cfg.ITERATIONS):
      if self._cfg.TRAIN and self._iteration >= self._cfg.ITERATIONS:
        print('Finished training (iteration %d)' % self._iteration)
        break
  
      # Receive data (this will hang if IO thread is still running = this will wait for thread to finish & receive data)
      batch_data   = self._filler.fetch_data(self._cfg.KEYWORD_DATA).data()
      batch_label  = self._filler.fetch_data(self._cfg.KEYWORD_LABEL).data()
      batch_weight = None
      # Start IO thread for the next batch while we train the network
      if self._cfg.TRAIN:
        if self._cfg.USE_WEIGHTS:
          batch_weight = self._filler.fetch_data(self._cfg.KEYWORD_WEIGHT).data()
          # perform per-event normalization
          batch_weight /= (np.sum(batch_weight,axis=1).reshape([batch_weight.shape[0],1]))
    
        _,loss,acc_all,acc_nonzero = self._net.train(sess         = sess, 
                                                     input_data   = batch_data,
                                                     input_label  = batch_label,
                                                     input_weight = batch_weight)
        self._iteration += 1
        msg = 'Training in progress @ step %d loss %g accuracy %g / %g \n'
        msg = msg % (self._iteration,loss,acc_all,acc_nonzero)
        sys.stdout.write(msg)
        maxval, minval, meanval = self._net.stats(sess = sess, 
                                                  input_data = batch_data,
                                                  input_label = batch_label,
                                                  input_weight = batch_weight)
        debug = 'max %g, min %g, mean %g \n'
        debug = debug % (np.squeeze(maxval), np.squeeze(minval), np.squeeze(meanval))
        sys.stdout.write(debug)
        sys.stdout.flush()

      else:
        softmax,acc_all,acc_nonzero = self._net.inference(sess        = sess,
                                                          input_data  = batch_data,
                                                          input_label = batch_label)
        print('Inference accuracy:', acc_all, '/', acc_nonzero)

        if self._drainer:

          entries   = self._filler.fetch_entries()
          event_ids = self._filler.fetch_event_ids()

          for entry in xrange(len(softmax)):

            print(entries[entry])
            print( event_ids[entry])

            self._drainer.read_entry(entry)
            data  = np.array(batch_data[entry]).reshape(softmax.shape[1:-1])
            print(data.event_key())
            label = np.array(batch_label[entry]).reshape(softmax.shape[1:-1])          
            shower_score = softmax[entry,:,:,:,1]
            track_score  = softmax[entry,:,:,:,2]
            
            sum_score = shower_score + track_score
            shower_score = shower_score / sum_score
            track_score  = track_score  / sum_score
            
            ssnet_result = (shower_score > track_score).astype(np.float32) + (track_score >= shower_score).astype(np.float32) * 2.0
            nonzero_map = (data > 1.0).astype(np.int32)
            ssnet_result = (ssnet_result * nonzero_map).astype(np.float32)
            #print(ssnet_result.shape,ssnet_result.max(),ssnet_result.min(),(ssnet_result<1).astype(np.int32).sum())
            #print(larcv.as_tensor3d(ssnet_result))

            data = self._drainer.get_data("sparse3d","data")
            sparse3d = self._drainer.get_data("sparse3d","ssnet")
            vs = larcv.as_tensor3d(ssnet_result)
            #sparse3d = vs
            #print( vs.as_vector().size())
            #for vs_index in xrange(vs.as_vector().size()):
            #  vox = vs.as_vector()[vs_index]
            #  sparse3d.add(vs.as_vector()[vs_index])
            sparse3d.set(vs,data.meta())
            self._drainer.save_entry()
            #self._drainer.clear_entry()
        
        if self._cfg.DUMP_IMAGE:
          for image_index in xrange(len(softmax)):
            event_image = softmax[image_index]
            bg_image = event_image[:,:,0]
            track_image = event_image[:,:,1]
            shower_image = event_image[:,:,2]
            bg_image_name = 'SOFTMAX_BG_%05d.png' % (i * self._cfg.BATCH_SIZE + image_index)
            track_image_name = 'SOFTMAX_TRACK_%05d.png' % (i * self._cfg.BATCH_SIZE + image_index)
            shower_image_name = 'SOFTMAX_SHOWER_%05d.png' % (i * self._cfg.BATCH_SIZE + image_index)
            
            fig,ax = plt.subplots(figsize=(12,8),facecolor='w')
            plt.imshow((bg_image * 255.).astype(np.uint8),vmin=0,vmax=255,cmap='jet',interpolation='none').write_png(bg_image_name)
            plt.close()

            fig,ax = plt.subplots(figsize=(12,8),facecolor='w')
            plt.imshow((shower_image * 255.).astype(np.uint8),vmin=0,vmax=255,cmap='jet',interpolation='none').write_png(shower_image_name)
            plt.close()
            
            fig,ax = plt.subplots(figsize=(12,8),facecolor='w')
            plt.imshow((track_image * 255.).astype(np.uint8),vmin=0,vmax=255,cmap='jet',interpolation='none').write_png(track_image_name)
            plt.close()

      # Save log
      if self._cfg.TRAIN and self._cfg.SUMMARY_STEPS and ((self._iteration+1)%self._cfg.SUMMARY_STEPS) == 0:
        # Run summary
        feed_dict = self._net.feed_dict(input_data   = batch_data,
                                        input_label  = batch_label,
                                        input_weight = batch_weight)
        writer.add_summary(sess.run(merged_summary,feed_dict=feed_dict),self._iteration)
  
      # Save snapshot
      if self._cfg.TRAIN and self._cfg.CHECKPOINT_STEPS and ((self._iteration+1)%self._cfg.CHECKPOINT_STEPS) == 0:
        # Save snapshot
        ssf_path = saver.save(sess,self._cfg.SAVE_FILE,global_step=self._iteration)
        print()
        print('saved @',ssf_path)

      self._filler.next(store_entries   = (not self._cfg.TRAIN),
                        store_event_ids = (not self._cfg.TRAIN))




    self._filler.reset()
    self._drainer.finalize()
    del self._filler
    #self._filler = None
