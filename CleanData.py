#-*- coding: utf-8 -*-
'''
数据清洗
'''

from pymongo import MongoClient
import pandas as pd
import time, datetime
import json
import logging
import os


LOG_FILE = os.getcwd() + '/' + 'LogFile/' + time.strftime('%Y-%m-%d',time.localtime(time.time()))  + ".log"
logging.basicConfig(filename=LOG_FILE, level=logging.DEBUG)
logger = logging.getLogger(__name__)

def add_log(func):
    def newFunc(*args, **kwargs):
        logger.warning("Before %s() call on %s" % (func.__name__, time.strftime("%Y-%m-%d %H:%M:%S")))
        ret = func(*args, **kwargs)
        logger.warning("After %s() call on %s" % (func.__name__, time.strftime("%Y-%m-%d %H:%M:%S")))
        return ret
    return newFunc


class CleanData(object):

    def __init__(self):
        timePoint = datetime.datetime.today() - datetime.timedelta(days=1)
        self.timePoint = timePoint.replace(hour=21, minute=00, second=00, microsecond=0)
        self.dfInfo = self.loadInformation()
        self.AucTime = ['8:59:00', '20:59:00', '9:29:00', '9:14:00']

    def initList(self):
        self.removeList = []
        self.updateList = []
        self.logList = []

    def initCleanRegulation(self):
        db = self.get_db("192.168.1.80", 27017, 'MTS_TICK_DB')
        dbNew = self.get_db("localhost", 27017, 'test_MTS_TICK_DB')
        names = self.get_all_colls(db)
        for i in names:
            if 'sc' in i:
                continue
            try:
                print "start process collection %s........." %(i)
                logger.warning("start process collection %s........." %(i))
                self.Symbol = filter(str.isalpha, str(i)).lower()
                self.df = pd.DataFrame(list(self.get_specificItems(db, i, self.timePoint)))
                self.initList()
                if not self.df.empty:
                    self.cleanIllegalTradingTime()
                    self.reserveLastTickInAuc()
                    self.cleanSameTimestamp()
                    self.cleanExceptionalPrice()
                    self.cleanNullVolTurn()
                    self.cleanNullPriceIndicator()
                    self.cleanNullOpenInter()
                    self.recordExceptionalPrice()

                    self.delItemsFromRemove()
                    self.insert2db(dbNew,i)
            except Exception, e:
                print e
                logger.error(e)
                continue

    def get_db(self,host,port,dbName):
        #建立连接
        client = MongoClient(host,port)
        db = client[dbName]
        return db

    def get_all_colls(self,db):
        return [i for i in db.collection_names()]

    def get_specificItems(self, db, coll_name, time):
        Items = db[coll_name].find({"datetime": {'$gte': time}})
        return Items

    def insert2db(self,dbNew,coll_name):
        del self.df["_id"]
        self.df = self.df.dropna(axis=0, how='all')
        data = json.loads(self.df.T.to_json(date_format = 'iso')).values()
        for i in data:
            if isinstance(i["datetime"], unicode):
                i["datetime"] = datetime.datetime.strptime(i["datetime"], "%Y-%m-%dT%H:%M:%S.%fZ")
        dbNew[coll_name].insert_many(data)

    def loadInformation(self):
        dfInfo = pd.read_csv(os.getcwd() + '/BasicInformation.csv')
        dfInfo.index = dfInfo['Symbol'].tolist()
        del dfInfo['Symbol']
        # 增加对历史周期交易时间段变更的记录
        dfInfo["CurrPeriod"] = dfInfo["TradingPeriod"].map(self.identifyCurrentPeriod)
        return dfInfo

    def identifyCurrentPeriod(self, target):
        if '%' in target:
            phase = [i for i in target.split('%')]
            phase.sort(reverse=True)
            for i in phase:
                startDate = datetime.datetime.strptime(i.split('||')[0], "%Y-%m-%d")
                if startDate <= self.timePoint:
                    return i.split('||')[1]
                else:
                    continue
        else:
            return target.split('||')[1]

    @add_log
    def cleanIllegalTradingTime(self):
        """删除非交易时段数据"""
        self.df['illegalTime'] = self.df["time"].map(self.StandardizeTimePeriod)
        self.df['illegalTime'] = self.df['illegalTime'].fillna(False)
        for i,row in self.df[self.df['illegalTime'] == False].iterrows():
            self.removeList.append(i)
            logger.info('remove index = %d, id = %s' %(i, row["_id"]))
        del self.df["illegalTime"]

    @add_log
    def reserveLastTickInAuc(self):
        """保留集合竞价期间最后一个tick数据"""
        self.df["structTime"] = self.df["time"].map(lambda x: datetime.datetime.strptime(x, "%H:%M:%S.%f"))
        for st in self.AucTime:
            start = datetime.datetime.strptime(st, '%H:%M:%S')
            end = start + datetime.timedelta(minutes=1)
            p1 = self.df["structTime"] >= start
            p2 = self.df["structTime"] < end
            dfTemp = self.df.loc[p1 & p2]
            dfTemp = dfTemp.sort_values(by=["structTime"], ascending=False)
            for i in dfTemp.index.values[1:]:
                self.removeList.append(i)
                logger.info('remove index = %d' % i)

    @add_log
    def cleanSameTimestamp(self):
        """清除重复时间戳，记录"""
        dfTemp = self.df.sort_values(by = ['datetime'], ascending = False)
        idList = dfTemp[dfTemp["datetime"].duplicated()].index
        for i in idList.values:
            self.removeList.append(i)
            logger.info('remove index = %d' % i)

    @add_log
    def cleanExceptionalPrice(self):
        """清理异常价格数据"""
        openP = self.df["openPrice"] >= 1e+308
        highP = self.df["highPrice"] >= 1e+308
        # settleP = self.df["settlementPrice"] >= 1e+308
        lowP = self.df["lowPrice"] >= 1e+308

        dfTemp = self.df.loc[openP | highP | lowP]
        for i, row in dfTemp.iterrows():
            if i not in self.removeList:
                self.removeList.append(i)
                logger.info('remove index = %d, id = %s' % (i, row["_id"]))

    @add_log
    def cleanNullVolTurn(self):
        """Tick有成交，但volume和turnover为0"""
        f = lambda x: float(x)
        self.df["lastVolume"] = self.df["lastVolume"].map(f)
        self.df["lastTurnover"] = self.df["lastTurnover"].map(f)
        self.df["volume"] = self.df["volume"].map(f)
        self.df["turnover"] = self.df["turnover"].map(f)
        self.df["openInterest"] = self.df["openInterest"].map(f)
        self.df["lastPrice"] = self.df["lastPrice"].map(f)

        lastVol = self.df["lastVolume"] != 0.0
        lastTurn = self.df["lastTurnover"] != 0.0
        Vol = self.df["volume"] == 0.0
        Turn = self.df["turnover"] == 0.0
        openIn = self.df["openInterest"] == 0.0
        lastP = self.df["lastPrice"] != 0.0

        tu = self.dfInfo.loc[self.Symbol]["TradingUnits"]

        # lastTurn为0,lastVolume和lastPrice不为0
        dfTemp = self.df.loc[~lastTurn & lastVol & lastP]
        if not dfTemp.empty:
            dfTemp["lastTurnover"] = dfTemp["lastVolume"] * dfTemp["lastPrice"] * float(tu)
            for i, row in dfTemp.iterrows():
                if i not in self.removeList:
                    self.df.loc[i,"lastTurnover"] = row["lastTurnover"]
                    self.updateList.append(i)
                    logger.info('lastTurn = 0, update index = %d, id = %s' % (i, row["_id"]))

        # lastVolume为0,lastTurnover和lastPrice不为0
        dfTemp = self.df.loc[lastTurn & ~lastVol & lastP]
        if not dfTemp.empty:
            dfTemp["lastVolume"] = dfTemp["lastTurnover"] / (dfTemp["lastPrice"] * float(tu))
            dfTemp["lastVolume"].map(lambda x:int(round(x)))
            for i, row in dfTemp.iterrows():
                if i not in self.removeList:
                    self.df.loc[i,"lastVolume"] = row["lastVolume"]
                    self.updateList.append(i)
                    logger.info('lastVol = 0, update index = %d, id = %s' % (i, row["_id"]))

        # lastPrice为0,lastVolume和lastTurnover不为0
        dfTemp = self.df.loc[lastTurn & lastVol & ~lastP]
        if not dfTemp.empty:
            dfTemp["lastPrice"] = dfTemp["lastTurnover"] / (dfTemp["lastVolume"] * float(tu))
            for i, row in dfTemp.iterrows():
                if i not in self.removeList:
                    self.df.loc[i,"lastPrice"] = row["lastPrice"]
                    self.updateList.append(i)
                    logger.info('lastPrice = 0, update index = %d, id = %s' % (i, row["_id"]))

        # lastVolume和lastTurnover均不为0
        dfTemp = self.df.loc[lastVol & lastTurn & (Vol | Turn | openIn)]
        if not dfTemp.empty:
            # volume、openInterest、turnover均为0，删除并记录
            if dfTemp.loc[Vol & Turn & openIn]._values.any():
                for i in dfTemp.loc[Vol & Turn & openIn].index.values:
                    if i not in self.removeList:
                        self.removeList.append(i)
                        self.logList.append(i)
                        logger.info('Vol & openInterest & turn = 0, remove index = %d' % i)

            # turnover为0,lastVol不为0
            for i, row in self.df[Turn & lastVol].iterrows():
                preIndex = i - 1
                if preIndex >= 0 and i not in self.removeList:
                    row["turnover"] = self.df.loc[preIndex,"turnover"] + row["lastTurnover"]
                    self.df.loc[i,"turnover"] = row["turnover"]
                    self.updateList.append(i)
                    logger.info('Turn = 0 & lastTurn != 0, update index = %d, id = %s' % (i, row["_id"]))

            # volume为0,lastVol不为0
            for i,row in self.df[Vol & lastVol].iterrows():
                preIndex = i - 1
                if preIndex >= 0 and i not in self.removeList:
                    row["volume"] = self.df.loc[preIndex,"volume"] + row["lastVolume"]
                    self.df.loc[i,"volume"] = row["volume"]
                    self.updateList.append(i)
                    logger.info('Vol = 0 & lastVol != 0, update index = %d, id = %s' % (i, row["_id"]))

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
            for i in self.df.loc[lastP & high & low & bidP & askP].index.values:
                if i not in self.removeList:
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
        self.df = self.df.drop(indexList,axis=0)

    def estimateExceptional(self,field):
        dfTemp = pd.DataFrame(self.df[field])
        dfTemp["_id"] = self.df["_id"]
        dfTemp["shift"] = self.df[field].shift(1)
        dfTemp["delta"] = abs(dfTemp[field] - dfTemp["shift"])
        dfTemp = dfTemp.dropna(axis=0, how='any')
        dfTemp["IsExcept"] = dfTemp["delta"] >= dfTemp["shift"] * 0.12
        for i, row in dfTemp.loc[dfTemp["IsExcept"]].iterrows():
            if i not in self.removeList:
                self.logList.append(i)
                logger.info('Field = %s, log index = %d, id = %s' % (field, i, row["_id"]))

    def paddingWithPrevious(self,field):
        for i, row in self.df.loc[self.df[field] == 0.0].iterrows():
            if i not in self.removeList:
                preIndex = i - 1
                while(preIndex in self.removeList or preIndex in self.updateList):
                    preIndex = preIndex - 1
                if preIndex >= 0 and i not in self.removeList:
                    row[field] = self.df.loc[preIndex,field]
                    self.df.loc[i,field] = row[field]
                    self.updateList.append(i)
                    logger.info('Field = %s, update index = %d, id = %s' % (field, i, row["_id"]))

    def StandardizeTimePeriod(self,target):
        tar = str(target)
        ms = 0
        try:
            tp = self.dfInfo.loc[self.Symbol]["CurrPeriod"]
            time1 = [t for i in tp.split(',') for t in i.split('-')]
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
        if s2 == time.strptime('00:00', '%H:%M'):
            s2 = time.strptime('23:59:61', '%H:%M:%S')
        if st > s1 and st < s2:
            return True
        elif (st == s1 and int(ms) >= 0) or (st == s2 and int(ms) == 0):
            return True
        else:
            return False

if __name__ == "__main__":
    ee = CleanData()
    ee.initCleanRegulation()
    print "Data Clean is completed........."
    logger.info("Data Clean is completed.........")
