import sys
from fileutil import *
from kazoo.client import KazooClient
import json
from collections import OrderedDict
import numpy as np
import xgboost
from sklearn.datasets import load_svmlight_file


class XGBoostSeldon:

    def __init__(self, conf):
        self.client = conf['client']
        self.awsKey = conf.get('awsKey',None)
        self.awsSecret = conf.get('awsSecret',None)
        self.zk_hosts = conf.get('zkHosts',None)
        if self.zk_hosts:
            print "connecting to zookeeper at ",self.zk_hosts
            self.zk_client = KazooClient(hosts=self.zk_hosts)
            self.zk_client.start()
        else:
            self.zk_client = None
        print "command line conf ",conf
        self.conf = self.__merge_conf(self.client,conf)
        print "conf after zookeeper merge ",conf
        self.svm_features = self.conf.get('svmFeatures',{})
        if 'target' in conf:
            self.target= self.conf['target']
        self.target_readable = self.conf.get('target_readable',None)
        


    def activateModel(self,client,folder):
        node = "/all_clients/"+client+"/xgboost"
        print "Activating model in zookeper at node ",node," with data ",folder
        if self.zk_client.exists(node):
            self.zk_client.set(node,folder)
        else:
            self.zk_client.create(node,folder,makepath=True)


    def __merge_conf(self,client,conf):
        thePath = "/all_clients/"+client+"/offline/xgboost"
        if self.zk_client and self.zk_client.exists(thePath):
            print "merging conf from zookeeper"
            data, stat = self.zk_client.get(thePath)
            zk_conf = json.loads(data.decode('utf-8'))
            zk_conf.update(conf)
            return zk_conf
        else:
            return conf


    
    def jsonToSvm(self,j,tag=None):
        if self.svm_features in j:
            line = str(j[self.target])
            d = j[self.svm_features]
            b = OrderedDict(sorted(d.items(), key=lambda t: float(t[0])))
            for k in b:
                line += (" "+str(k)+":"+str(b[k]))
            return line
        else:
            return None

    def process(self,line):
        j = json.loads(line)
        svmLine = self.jsonToSvm(j)
        self.numLinesProcessed += 1
        if svmLine:
            if self.target_readable:
                self.target_map[j[self.target]] = j[self.target_readable]
            if self.train_file:
                self.train_file.write(svmLine+"\n")
        
    def save_target_map(self):
        v = json.dumps(self.target_map,sort_keys=True)
        f = open('./target_map.json',"w")
        f.write(v)
        f.close()

    def train_model(self):
        train_X, train_Y = load_svmlight_file("train.svm")
        xg_train = xgboost.DMatrix(train_X,label=train_Y)
        # setup parameters for xgboost
        param = {}
        #    param['objective'] = 'multi:softmax'
        param['objective'] = 'multi:softprob'
        # scale weight of positive examples
        param['eta'] = 0.1
        param['max_depth'] = 15
        param['silent'] = 1
        param['nthread'] = 6
        param['num_class'] = len(self.target_map) + 1
        watchlist = [ (xg_train,'train') ]
        num_round = 5
        bst = xgboost.train(param, xg_train, num_round, watchlist );
        bst.save_model('model')

    def train(self):
        self.numLinesProcessed = 0
        self.target_map = {}
        self.train_file = open("train.svm","w")

        #stream data into vw
        inputPath = self.conf["inputPath"] + "/" + self.client + "/features/" + str(self.conf['day']) + "/"
        print "inputPath->",inputPath
        fileUtil = FileUtil(key=self.awsKey,secret=self.awsSecret)
        fileUtil.stream(inputPath,self.process)
        self.train_file.close()
        
        self.train_model()

        self.save_target_map()
        # copy models to final location
        outputPath = self.conf["outputPath"] + "/" + self.client + "/xgboost/" + str(self.conf["day"])
        print "outputPath->",outputPath
        fileUtil.copy("./model",outputPath+"/model")
        fileUtil.copy("./target_map.json",outputPath+"/target_map.json")

        #activate model in zookeeper
        if "activate" in self.conf and self.conf["activate"]:
            self.activateModel(self.client,str(outputPath))