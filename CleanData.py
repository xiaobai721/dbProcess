#-*- coding: utf-8 -*-
'''
数据清洗
'''

from pymongo import MongoClient
import pandas as pd
import time
import json
import logging
import os


LOG_FILE = os.getcwd() + '/' + 'LogFile/' + time.strftime('%Y-%m-%d',time.localtime(time.time()))  + ".log"
logging.basicConfig(filename=LOG_FILE, level=logging.DEBUG)
logger = logging.getLogger(__name__)

def add_log(func):
    def newFunc(*args, **kwargs):
        logger.debug("Before %s() call on %s" % (func.__name__, time.strftime("%Y-%m-%d %H:%M:%S")))
        ret = func(*args, **kwargs)
        logger.debug("After %s() call on %s" % (func.__name__, time.strftime("%Y-%m-%d %H:%M:%S")))
        return ret
    return newFunc


class CleanData(object):

    LOG_FILE = "test.log"
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO)
    logger = logging.getLogger(__name__)

    def __init__(self):

        self.dfInfo = self.loadInformation()
        self.removeList = []
        self.updateList = []
        self.logList = []
        # self.initCleanRegulation()

    def initCleanRegulation(self):
        db = self.get_db("localhost", 27017, 'MTS_TICK_DB')
        dbNew = self.get_db("localhost", 27017, 'test')

        names = self.get_all_colls(db)
        for i in names:
            print "start process collection %s........." %(i)
            logger.info("start process collection %s........." %(i))
            self.Symbol = filter(str.isalpha, str(i)).lower()
            self.df = pd.DataFrame(list(self.get_items(db, i)))
            self.cleanIllegalTradingTime()
            self.cleanSameTimestamp()
            self.cleanNullVolTurn()
            self.cleanNullOpenInter()
            self.cleanNullPriceIndicator()
            self.recordExceptionalPrice()

            self.delItemsFromRemove()
            self.insert2db(dbNew,i)

    def get_db(self,host,port,dbName):
        #建立连接
        client = MongoClient(host,port)
        db = client[dbName]
        return db

    def get_all_colls(self,db):
        return [i for i in db.collection_names()]

    def get_items(self,db,coll_name):
        Items = db[coll_name].find()
        return Items

    def insert2db(self,dbNew,coll_name):
        del self.df["_id"]
        data = json.loads(self.df.T.to_json()).values()
        dbNew[coll_name].insert_many(data)

    def loadInformation(self):
        dfInfo = pd.read_csv(os.getcwd() + '/BasicInformation.csv')
        dfInfo.index = dfInfo['Symbol'].tolist()
        del dfInfo['Symbol']
        return dfInfo

    @add_log
    def cleanIllegalTradingTime(self):
        """删除非交易时段数据"""
        self.df['illegalTime'] = self.df["time"].map(self.StandardizeTimePeriod)
        self.df['illegalTime'] = self.df['illegalTime'].fillna(False)
        for i,row in self.df[self.df['illegalTime'] == True].iterrows():
            self.removeList.append(i)
            logger.info('remove index = %d' %i)
        del self.df["illegalTime"]

    @add_log
    def cleanSameTimestamp(self):
        """清除重复时间戳，记录"""
        idList = self.df[self.df["datetime"].duplicated()].index
        for i in idList.values:
            self.removeList.append(i)
            logger.info('remove index = %d' % i)

    @add_log
    def cleanNullVolTurn(self):
        """Tick有成交，但volume和turnover为0"""
        f = lambda x: float(x)
        self.df["lastVolume"].map(f)
        self.df["lastTurnover"].map(f)
        self.df["volume"].map(f)
        self.df["turnover"].map(f)
        self.df["openInterest"].map(f)
        self.df["lastPrice"].map(f)

        lastVol = self.df["lastVolume"] != 0.0
        lastTurn = self.df["lastTurnover"] != 0.0
        Vol = self.df["volume"] == 0.0
        Turn = self.df["turnover"] == 0.0
        openIn = self.df["openInterest"] == 0.0
        lastP = self.df["lastPrice"] != 0.0

        # lastTurn为0,lastVolume和lastPrice不为0
        dfTemp = self.df.loc[~lastTurn & lastVol & lastP]
        dfTemp.loc[:,"lastTurnover"] = dfTemp.loc[:,"lastVolume"] * dfTemp.loc[:,"lastPrice"]
        for i, row in dfTemp.iterrows():
            self.df.loc[i,"lastTurnover"] = row["lastTurnover"]
            self.updateList.append(i)
            logger.info('lastTurn = 0, update index = %d' % i)

        # lastVolume为0,lastTurnover和lastPrice不为0
        dfTemp = self.df.loc[lastTurn & ~lastVol & lastP]
        dfTemp.loc[:,"lastVolume"] = dfTemp.loc[:,"lastTurnover"] / dfTemp.loc[:,"lastPrice"]
        for i, row in dfTemp.iterrows():
            self.df.loc[i,"lastVolume"] = row["lastVolume"]
            self.updateList.append(i)
            logger.info('lastVol = 0, update index = %d' % i)

        # lastPrice为0,lastVolume和lastTurnover不为0
        dfTemp = self.df.loc[lastTurn & lastVol & ~lastP]
        dfTemp.loc[:,"lastPrice"] = dfTemp.loc[:,"lastTurnover"] / dfTemp.loc[:,"lastVolume"]
        for i, row in dfTemp.iterrows():
            self.df.loc[i,"lastPrice"] = row["lastPrice"]
            self.updateList.append(i)
            logger.info('lastPrice = 0, update index = %d' % i)

        # lastVolume和lastTurnover均不为0
        dfTemp = self.df.loc[lastVol & lastTurn & (Vol | Turn | openIn)]

        # volume、openInterest、turnover均为0，删除并记录
        if dfTemp.loc[Vol & Turn & openIn]._values.any():
            self.removeList.extend(i for i in dfTemp.loc[Vol & Turn & openIn].index.values)
            self.logList.extend(i for i in dfTemp.loc[Vol & Turn & openIn].index.values)

        # turnover为0,lastVol不为0
        for i, row in self.df[Turn & lastVol].iterrows():
            preIndex = i - 1
            if preIndex >= 0:
                row["turnover"] = self.df.loc[preIndex,"turnover"] + row["lastTurnover"]
                self.df.loc[i,"turnover"] = row["turnover"]
                self.updateList.append(i)
                logger.info('Turn = 0 & lastTurn != 0, update index = %d' % i)

        # volume为0,lastVol不为0
        for i,row in self.df[Vol & lastVol].iterrows():
            preIndex = i - 1
            if preIndex >= 0:
                row["volume"] = self.df.loc[preIndex,"volume"] + row["lastVolume"]
                self.df.loc[i,"volume"] = row["volume"]
                self.updateList.append(i)
                logger.info('Vol = 0 & lastVol != 0, update index = %d' % i)

    @add_log
    def cleanNullOpenInter(self):
        """持仓量为0,用上一个填充"""
        self.paddingWithPrevious("openInterest")

    @add_log
    def cleanNullPriceIndicator(self):
        lastP = self.df["lastPrice"] == 0.0
        high = self.df["highPrice"] == 0.0
        low = self.df["lowPrice"] == 0.0
        bidP = self.df["bidPrice1"] == 0.0
        askP = self.df["askPrice1"] == 0.0
        #如果均为0，删除
        if self.df.loc[lastP & high & low & bidP & askP]._values.any():
            # self.removeList.extend(i for i in self.df.loc[lastP & high & low & bidP & askP].index.values)
            for i in self.df.loc[lastP & high & low & bidP & askP].index.values:
                self.removeList.append(i)
                logger.info('All Price is Null, remove index = %d' %i)

        # 某些为0，填充
        self.paddingWithPrevious("lastPrice")
        self.paddingWithPrevious("highPrice")
        self.paddingWithPrevious("lowPrice")
        self.paddingWithPrevious("bidPrice1")
        self.paddingWithPrevious("askPrice1")

    @add_log
    def recordExceptionalPrice(self):
        self.estimateExceptional("lastPrice")
        self.estimateExceptional("highPrice")
        self.estimateExceptional("lowPrice")
        self.estimateExceptional("bidPrice1")
        self.estimateExceptional("askPrice1")

    def delItemsFromRemove(self):
        indexList = list(set(self.removeList))
        self.df.drop(indexList,axis=0)

    def estimateExceptional(self,field):
        dfTemp = pd.DataFrame(self.df[field])
        dfTemp["_id"] = self.df["_id"]
        dfTemp["shift"] = self.df[field].shift(1)
        dfTemp["delta"] = abs(dfTemp[field] - dfTemp["shift"])
        dfTemp = dfTemp.dropna(axis=0, how='any')
        dfTemp["IsExcept"] = dfTemp["delta"] >= dfTemp["shift"] * 0.05
        for i, row in dfTemp.loc[dfTemp["IsExcept"]].iterrows():
            self.logList.append(i)
            logger.info('log index = %d' % i)

    def paddingWithPrevious(self,field):
        for i, row in self.df.loc[self.df[field] == 0.0].iterrows():
            if row["_id"] not in self.removeList:
                preIndex = i - 1
                if preIndex >= 0:
                    row[field] = self.df.loc[preIndex,field]
                    self.df.loc[i,field] = row[field]
                    self.updateList.append(i)
                    logger.info('Field = %s, update index = %d' % (field, i))

    def StandardizeTimePeriod(self,target):
        tar = target
        ms = 0
        try:
            tp = self.dfInfo.loc[self.dfInfo["Symbol"] == self.Symbol]["TradingPeriod"]
            time1 = [t for i in tp[0].split(',') for t in i.split('-')]
            if '.' in tar:
                ms = tar.split('.')[1]
                tar = tar.split('.')[0]

            tar = time.strptime(tar, '%H:%M:%S')
            for i in zip(*([iter(time1)] * 2)):
                start = time.strptime(str(i[0]).strip(), '%H:%M')
                end = time.strptime(str(i[1]).strip(), '%H:%M')
                if self.compare_time(start,end,tar,ms):
                    return True

        except Exception, e:
            print e

    def compare_time(self,s1,s2,st,ms):
        """由于time类型没有millisecond，故单取ms进行逻辑判断"""
        if st > s1 and st < s2:
            return True
        elif (st == s1 and ms == 0) or (st == s2 and ms == 0):
            return True
        else:
            return False



if __name__ == "__main__":
    ee = CleanData()
    ee.initCleanRegulation()
