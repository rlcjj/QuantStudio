# coding=utf-8
"""TODO"""
import datetime as dt
import base64
from io import BytesIO

import numpy as np
import pandas as pd
from traits.api import ListStr, Enum, List, Int, Dict, Bool
from traitsui.api import SetEditor, Item
import statsmodels.api as sm
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.ticker import FuncFormatter

from QuantStudio import __QS_Error__
from QuantStudio.Tools.AuxiliaryFun import getFactorList, searchNameInStrList
from QuantStudio.Tools.MathFun import CartesianProduct
from QuantStudio.BackTest.BackTestModel import BaseModule


class LinerRegression(BaseModule):
    """时间序列线性回归"""
    TestFactors = ListStr(arg_type="MultiOption", label="测试因子", order=0, option_range=())
    #PriceFactor = Enum(None, arg_type="SingleOption", label="价格因子", order=1)
    ReturnType = Enum("简单收益率", "对数收益率", "价格变化量", arg_type="SingleOption", label="收益率类型", order=2)
    ForecastPeriod = Int(1, arg_type="Integer", label="预测期数", order=3)
    IntervalPeriod = Int(0, arg_type="Integer", label="间隔期数", order=4)
    CalcDTs = List(dt.datetime, arg_type="DateList", label="计算时点", order=5)
    SummaryWindow = Int(np.inf, arg_type="Integer", label="统计窗口", order=6)
    MinSummaryWindow = Int(2, arg_type="Integer", label="最小统计窗口", order=7)
    RegressMethod = Enum("OLS", "WLS", "GLS", "LASSO", "Ridge", arg_type="SingleOption", label="回归算法", order=8)
    Constant = Bool(True, arg_type="Bool", label="常数项", order=9)
    RegressArgs = Dict(arg_type="SingleOption", label="算法参数", order=10)
    def __init__(self, factor_table, price_table, name="时间序列线性回归", sys_args={}, **kwargs):
        self._FactorTable = factor_table
        self._PriceTable = price_table
        super().__init__(name=name, sys_args=sys_args, **kwargs)
    def __QS_initArgs__(self):
        DefaultNumFactorList, DefaultStrFactorList = getFactorList(dict(self._FactorTable.getFactorMetaData(key="DataType")))
        self.add_trait("TestFactors", ListStr(arg_type="MultiOption", label="测试因子", order=0, option_range=tuple(DefaultNumFactorList)))
        self.TestFactors.append(DefaultNumFactorList[0])
        DefaultNumFactorList, DefaultStrFactorList = getFactorList(dict(self._PriceTable.getFactorMetaData(key="DataType")))
        self.add_trait("PriceFactor", Enum(*DefaultNumFactorList, arg_type="SingleOption", label="价格因子", order=1))
        self.PriceFactor = searchNameInStrList(DefaultNumFactorList, ['价','Price','price'])
    def getViewItems(self, context_name=""):
        Items, Context = super().getViewItems(context_name=context_name)
        Items[0].editor = SetEditor(values=self.trait("TestFactors").option_range)
        return (Items, Context)
    def __QS_start__(self, mdl, dts, **kwargs):
        if self._isStarted: return ()
        super().__QS_start__(mdl=mdl, dts=dts, **kwargs)
        self._Output = {}
        self._Output["证券ID"] = self._PriceTable.getID()
        nID = len(self._Output["证券ID"])
        self._Output["收益率"] = np.zeros(shape=(0, len(self._Output["证券ID"])))
        self._Output["滚动统计量"] = {"R平方":np.zeros((0, nID)), "调整R平方":np.zeros((0, nID)), "t统计量":{}, "F统计量":np.zeros((0, nID))}
        self._Output["因子ID"] = self._FactorTable.getID()
        nFactorID = len(self._Output["因子ID"])
        self._Output["因子值"] = np.zeros((0, nFactorID*len(self.TestFactors)))
        self._CurCalcInd = 0
        self._nMinSample = (max(2, self.MinSummaryWindow) if np.isinf(self.MinSummaryWindow) else max(2, self.MinSummaryWindow))
        return (self._FactorTable, self._PriceTable)
    def __QS_move__(self, idt, **kwargs):
        if self._iDT==idt: return 0
        if self.CalcDTs:
            if idt not in self.CalcDTs[self._CurCalcInd:]: return 0
            self._CurCalcInd = self.CalcDTs[self._CurCalcInd:].index(idt) + self._CurCalcInd
            PreInd = self._CurCalcInd - self.ForecastPeriod - self.IntervalPeriod
            LastInd = self._CurCalcInd - self.ForecastPeriod
            PreDateTime = self.CalcDTs[PreInd]
            LastDateTime = self.CalcDTs[LastInd]
        else:
            self._CurCalcInd = self._Model.DateTimeIndex
            PreInd = self._CurCalcInd - self.ForecastPeriod - self.IntervalPeriod
            LastInd = self._CurCalcInd - self.ForecastPeriod
            PreDateTime = self._Model.DateTimeSeries[PreInd]
            LastDateTime = self._Model.DateTimeSeries[LastInd]
        if (PreInd<0) or (LastInd<0): return 0
        Price = self._PriceTable.readData(dts=[LastDateTime, idt], ids=self._Output["证券ID"], factor_names=[self.PriceFactor]).iloc[0, :, :].values
        if self.ReturnType=="对数收益率": Return = np.log(Price[-1]) - np.log(Price[0])
        elif self.ReturnType=="价格变化量": Return = Price[-1] - Price[0]
        else: Return = Price[-1] / Price[0] - 1
        self._Output["收益率"] = np.r_[self._Output["收益率"], Return.reshape((1, Return.shape[0]))]
        FactorData = self._FactorTable.readData(dts=[PreDateTime], ids=self._Output["因子ID"], factor_names=list(self.TestFactors)).iloc[:, 0, :].values.flatten(order="F")
        self._Output["因子值"] = np.r_[self._Output["因子值"], FactorData.reshape((1, FactorData.shape[0]))]
        if self._Output["收益率"].shape[0]<self._nMinSample: return 0
        StartInd = max(0, self._Output["收益率"].shape[0] - self.SummaryWindow)
        X = self._Output["因子值"][StartInd:]
        if self.Constant: X = sm.add_constant(X, prepend=True)
        nID = len(self._Output["证券ID"])
        Statistics = {"R平方":np.full((1, nID), np.nan), "调整R平方":np.full((1, nID), np.nan), "t统计量":np.full((X.shape[1], nID), np.nan), "F统计量":np.full((1, nID), np.nan)}
        for i, iID in enumerate(self._Output["证券ID"]):
            Y = self._Output["收益率"][StartInd:, i]
            try:
                Result = sm.OLS(X, Y, missing="drop").fit()
            except:
                continue
            Statistics["R平方"][0, i] = Result.rsquared
            Statistics["调整R平方"][0, i] = Result.rsquared_adj
            Statistics["F统计量"][0, i] = Result.fvalue
            Statistics["t统计量"][:, i] = Result.tvalues
        self._Output["滚动统计量"]["R平方"] = np.r_[self._Output["滚动统计量"]["R平方"], Statistics["R平方"]]
        self._Output["滚动统计量"]["调整R平方"] = np.r_[self._Output["滚动统计量"]["调整R平方"], Statistics["调整R平方"]]
        self._Output["滚动统计量"]["F统计量"] = np.r_[self._Output["滚动统计量"]["F统计量"], Statistics["F统计量"]]
        self._Output["滚动统计量"]["t统计量"][idt] = Statistics["t统计量"]
        return 0
    def __QS_end__(self):
        if not self._isStarted: return 0
        FactorIDs, PriceIDs = self._Output.pop("因子ID"), self._Output.pop("证券ID")
        LastDT = max(self._Output["滚动统计量"]["t统计量"])
        self._Output["最后一期统计量"] = pd.DataFrame({"R平方": self._Output["滚动统计量"]["R平方"][-1], "调整R平方": self._Output["滚动统计量"]["调整R平方"][-1],
                                                      "F统计量": self._Output["滚动统计量"]["F统计量"][-1]}, index=self._Output.pop("证券ID")).loc[:, ["R平方", "调整R平方", "F统计量"]]
        self._Output["最后一期t统计量"] = pd.DataFrame(self._Output["滚动统计量"]["t统计量"][LastDT], index=["Constant"])# TODO
        for iFactorName in self.TestFactors:
            self._Output["最后一期统计量"][iFactorName] = self._Output["滚动统计量"][iFactorName][LastDT].T
            self._Output["全样本统计量"][iFactorName] = pd.DataFrame(np.c_[self._Output["因子值"][iFactorName], self._Output["收益率"]]).corr(method=self.CorrMethod, min_periods=self._nMinSample).values[:len(FactorIDs), len(FactorIDs):].T
            self._Output["滚动相关性"][iFactorName] = pd.Panel(self._Output["滚动相关性"][iFactorName], major_axis=FactorIDs, minor_axis=PriceIDs).swapaxes(0, 2).to_frame(filter_observations=False).reset_index()
            self._Output["滚动相关性"][iFactorName].columns = ["因子ID", "时点"]+PriceIDs
        self._Output["最后一期统计量"] = pd.Panel(self._Output["最后一期统计量"], major_axis=PriceIDs, minor_axis=FactorIDs).swapaxes(0, 1).to_frame(filter_observations=False).reset_index()
        self._Output["最后一期统计量"].columns = ["因子", "因子ID"]+PriceIDs
        self._Output["全样本统计量"] = pd.Panel(self._Output["全样本统计量"], major_axis=PriceIDs, minor_axis=FactorIDs).swapaxes(0, 1).to_frame(filter_observations=False).reset_index()
        self._Output["全样本统计量"].columns = ["因子", "因子ID"]+PriceIDs
        self._Output.pop("收益率"), self._Output.pop("因子值")
        return 0