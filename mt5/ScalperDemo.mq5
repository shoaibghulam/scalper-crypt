#property strict
#property version "1.00"
#property description "Demo-only EMA/RSI baseline scalper. No live-account execution."
#include <Trade/Trade.mqh>

input bool EnableEntries=false;
input ENUM_TIMEFRAMES SignalTimeframe=PERIOD_M1;
input int FastEMA=9;
input int SlowEMA=21;
input int RSIPeriod=14;
input int ATRPeriod=14;
input double RiskPercent=0.25;
input double StopATR=1.5;
input double RewardRisk=1.5;
input int MaxSpreadPoints=20;
input double MaxSpreadToStop=0.20;
input int DeviationPoints=5;
input int MaxHoldMinutes=15;
input double DailyLossPercent=2.0;
input double MaxLots=0.10;
input ulong Magic=26092001;

CTrade trade;
int fastHandle=INVALID_HANDLE,slowHandle=INVALID_HANDLE;
int rsiHandle=INVALID_HANDLE,atrHandle=INVALID_HANDLE;
datetime lastBar=0;
string displayStatus="Initializing",guardKey="";
bool uncertain=false;

bool DemoAllowed()
{
   return (bool)MQLInfoInteger(MQL_TESTER) ||
          AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO;
}

void Show(string message)
{
   if(displayStatus!=message) Print("ScalperDemo: ",message);
   displayStatus=message;
   Comment("SCALPER DEMO | ",_Symbol," | ",EnumToString(SignalTimeframe),
           "\nEntries: ",EnableEntries ? "enabled" : "disabled",
           " | Risk: ",DoubleToString(RiskPercent,2),"%",
           "\n",displayStatus,"\nEMA/RSI research baseline; profitability unproven.");
}

double RoundPrice(double value,bool up)
{
   double step=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(step<=0) return 0;
   return NormalizeDouble((up ? MathCeil(value/step) : MathFloor(value/step))*step,_Digits);
}

bool Permissions()
{
   return DemoAllowed() && TerminalInfoInteger(TERMINAL_CONNECTED) &&
          TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) && MQLInfoInteger(MQL_TRADE_ALLOWED) &&
          AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) && AccountInfoInteger(ACCOUNT_TRADE_EXPERT);
}

bool Successful()
{
   uint code=trade.ResultRetcode();
   return code==TRADE_RETCODE_DONE || code==TRADE_RETCODE_DONE_PARTIAL;
}

void ManagePositions()
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || PositionGetString(POSITION_SYMBOL)!=_Symbol ||
         (ulong)PositionGetInteger(POSITION_MAGIC)!=Magic) continue;
      bool unprotected=PositionGetDouble(POSITION_SL)<=0 || PositionGetDouble(POSITION_TP)<=0;
      bool expired=TimeCurrent()-(datetime)PositionGetInteger(POSITION_TIME)>=MaxHoldMinutes*60;
      if(!unprotected && !expired) continue;
      if(!Permissions()) { Show("Cannot manage exit: trading permissions unavailable"); continue; }
      bool sent=trade.PositionClose(ticket);
      if(!sent || !Successful())
         Show("Exit not confirmed: "+trade.ResultRetcodeDescription());
      else Show(unprotected ? "Closed position with missing protection" : "Time exit executed");
   }
}

bool DailyRiskAllowed()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(),dt);
   dt.hour=0; dt.min=0; dt.sec=0;
   if(!HistorySelect(StructToTime(dt),TimeCurrent())) return false;
   double net=0;
   for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong ticket=HistoryDealGetTicket(i);
      if(ticket==0) return false;
      // Include all account cashflows; reconstruct balance at broker midnight.
      net+=HistoryDealGetDouble(ticket,DEAL_PROFIT)+HistoryDealGetDouble(ticket,DEAL_COMMISSION)
           +HistoryDealGetDouble(ticket,DEAL_SWAP)+HistoryDealGetDouble(ticket,DEAL_FEE);
   }
   double startBalance=AccountInfoDouble(ACCOUNT_BALANCE)-net;
   if(startBalance<=0) return false;
   return AccountInfoDouble(ACCOUNT_EQUITY)>startBalance*(1-DailyLossPercent/100);
}

bool ClaimBar(datetime bar)
{
   if(MQLInfoInteger(MQL_TESTER)) return true;
   if(!GlobalVariableCheck(guardKey))
   {
      // First attachment only initializes the lock; never races to place an order.
      GlobalVariableSet(guardKey,(double)bar);
      GlobalVariablesFlush();
      return false;
   }
   double previous=GlobalVariableGet(guardKey);
   if(previous>=(double)bar) return false;
   bool claimed=GlobalVariableSetOnCondition(guardKey,(double)bar,previous);
   if(claimed) GlobalVariablesFlush();
   return claimed;
}

void Evaluate(datetime bar)
{
   if(!EnableEntries) { Show("EnableEntries is false; monitoring only"); return; }
   if(uncertain) { Show("Order status uncertain: inspect Trade/History before reattaching EA"); return; }
   if(!Permissions()) { Show("Demo trading permissions or connection unavailable"); return; }
   // One position/order account-wide, including manual and other EA activity.
   if(PositionsTotal()>0 || OrdersTotal()>0) { Show("Waiting: account already has a position/order"); return; }
   if(!DailyRiskAllowed()) { Show("Daily equity loss limit or unavailable history: no new entries"); return; }
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0 || tick.ask<tick.bid ||
      TimeCurrent()-tick.time>10) { Show("No fresh valid quote"); return; }
   double spread=tick.ask-tick.bid;
   if(spread>MaxSpreadPoints*_Point) { Show("Spread above configured maximum"); return; }
   double fast[],slow[],rsi[],atr[];
   if(BarsCalculated(slowHandle)<SlowEMA+2 || BarsCalculated(fastHandle)<FastEMA+2 ||
      BarsCalculated(rsiHandle)<RSIPeriod+2 || BarsCalculated(atrHandle)<ATRPeriod+2)
   { Show("Waiting for indicator warm-up"); return; }
   // CopyBuffer puts the older closed bar first: [0]=shift 2, [1]=shift 1.
   if(CopyBuffer(fastHandle,0,1,2,fast)!=2 || CopyBuffer(slowHandle,0,1,2,slow)!=2 ||
      CopyBuffer(rsiHandle,0,1,1,rsi)!=1 || CopyBuffer(atrHandle,0,1,1,atr)!=1)
   { Show("Waiting for indicator history"); return; }
   for(int i=0;i<2;i++)
      if(!MathIsValidNumber(fast[i]) || !MathIsValidNumber(slow[i]) ||
         fast[i]==EMPTY_VALUE || slow[i]==EMPTY_VALUE)
      { Show("Invalid EMA values"); return; }
   if(!MathIsValidNumber(rsi[0]) || rsi[0]<0 || rsi[0]>100 || atr[0]==EMPTY_VALUE)
   { Show("Invalid RSI/ATR values"); return; }
   bool buy=fast[0]<=slow[0] && fast[1]>slow[1] && rsi[0]>=50 && rsi[0]<=70;
   bool sell=fast[0]>=slow[0] && fast[1]<slow[1] && rsi[0]<=50 && rsi[0]>=30;
   if(!buy && !sell) { Show("Waiting for closed-bar EMA cross and RSI confirmation"); return; }
   if(!MathIsValidNumber(atr[0]) || atr[0]<=0) { Show("Invalid ATR"); return; }
   long mode=SymbolInfoInteger(_Symbol,SYMBOL_TRADE_MODE);
   if(mode==SYMBOL_TRADE_MODE_DISABLED || mode==SYMBOL_TRADE_MODE_CLOSEONLY ||
      (buy && mode==SYMBOL_TRADE_MODE_SHORTONLY) || (sell && mode==SYMBOL_TRADE_MODE_LONGONLY))
   { Show("Broker does not permit this direction"); return; }
   long flags=SymbolInfoInteger(_Symbol,SYMBOL_ORDER_MODE);
   if((flags&SYMBOL_ORDER_MARKET)==0 || (flags&SYMBOL_ORDER_SL)==0 || (flags&SYMBOL_ORDER_TP)==0)
   { Show("Symbol must support market orders with SL and TP"); return; }
   double entry=buy ? tick.ask : tick.bid;
   double minimum=(SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)+2)*_Point;
   double distance=MathMax(atr[0]*StopATR,minimum+spread);
   if(spread/distance>MaxSpreadToStop) { Show("Spread consumes too much of the stop distance"); return; }
   double stop=RoundPrice(entry+(buy ? -distance : distance),!buy);
   double target=RoundPrice(entry+(buy ? 1 : -1)*MathAbs(entry-stop)*RewardRisk,buy);
   if(stop<=0 || target<=0) { Show("Invalid protective prices"); return; }
   ENUM_ORDER_TYPE side=buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double minLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   double maxLot=MathMin(MaxLots,SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX));
   double minLoss=0;
   if(minLot<=0 || step<=0 || !OrderCalcProfit(side,_Symbol,minLot,entry,stop,minLoss) || minLoss>=0)
   { Show("Cannot calculate broker lot risk"); return; }
   double budget=AccountInfoDouble(ACCOUNT_EQUITY)*RiskPercent/100;
   double raw=budget/MathAbs(minLoss)*minLot;
   double lots=NormalizeDouble(MathFloor(MathMin(raw,maxLot)/step)*step,8);
   if(lots<minLot) { Show("Minimum lot exceeds risk budget; increase demo balance or skip"); return; }
   double loss=0,margin=0;
   if(!OrderCalcProfit(side,_Symbol,lots,entry,stop,loss) || MathAbs(loss)>budget+0.000001 ||
      !OrderCalcMargin(side,_Symbol,lots,entry,margin) || margin>AccountInfoDouble(ACCOUNT_MARGIN_FREE)*0.8)
   { Show("Risk or free-margin check failed"); return; }
   if(!ClaimBar(bar)) { Show("This signal bar has already been processed"); return; }
   bool sent=trade.PositionOpen(_Symbol,side,lots,entry,stop,target,"ScalperDemo EMA RSI");
   if(!sent || !Successful())
   {
      // Ambiguous responses never trigger automatic entry retries.
      uncertain=true;
      Show("Entry not confirmed: "+trade.ResultRetcodeDescription());
      return;
   }
   Show("Demo entry filled; SL/TP submitted; check Trade tab");
   PrintFormat("DEMO ENTRY %s lots=%.8f requested=%.8f SL=%.8f TP=%.8f retcode=%u deal=%I64u",
               buy ? "BUY" : "SELL",lots,entry,stop,target,trade.ResultRetcode(),trade.ResultDeal());
   ManagePositions();
}

int OnInit()
{
   if(!DemoAllowed()) { Print("ScalperDemo REFUSED: only DEMO accounts or Strategy Tester allowed"); return INIT_FAILED; }
   if(FastEMA<2 || SlowEMA<=FastEMA || RSIPeriod<2 || ATRPeriod<2 ||
      RiskPercent<=0 || RiskPercent>1 || DailyLossPercent<=0 || DailyLossPercent>10 ||
      StopATR<=0 || RewardRisk<1 || MaxSpreadPoints<1 || MaxSpreadToStop<=0 ||
      MaxSpreadToStop>1 || DeviationPoints<0 || MaxHoldMinutes<1 || MaxLots<=0 || Magic==0 ||
      (SignalTimeframe!=PERIOD_M1 && SignalTimeframe!=PERIOD_M5)) return INIT_PARAMETERS_INCORRECT;
   fastHandle=iMA(_Symbol,SignalTimeframe,FastEMA,0,MODE_EMA,PRICE_CLOSE);
   slowHandle=iMA(_Symbol,SignalTimeframe,SlowEMA,0,MODE_EMA,PRICE_CLOSE);
   rsiHandle=iRSI(_Symbol,SignalTimeframe,RSIPeriod,PRICE_CLOSE);
   atrHandle=iATR(_Symbol,SignalTimeframe,ATRPeriod);
   if(fastHandle==INVALID_HANDLE || slowHandle==INVALID_HANDLE ||
      rsiHandle==INVALID_HANDLE || atrHandle==INVALID_HANDLE) return INIT_FAILED;
   trade.SetExpertMagicNumber(Magic);
   trade.SetDeviationInPoints(DeviationPoints);
   trade.SetAsyncMode(false);
   if(!trade.SetTypeFillingBySymbol(_Symbol)) return INIT_FAILED;
   guardKey=StringFormat("ScalperDemo.%I64d.%s",AccountInfoInteger(ACCOUNT_LOGIN),_Symbol);
   lastBar=iTime(_Symbol,SignalTimeframe,0);
   Show("Ready: waiting for the next closed candle");
   return INIT_SUCCEEDED;
}

void OnTick()
{
   if(!DemoAllowed()) { Show("Real account detected: execution blocked"); return; }
   ManagePositions();
   datetime bar=iTime(_Symbol,SignalTimeframe,0);
   if(bar<=0 || bar==lastBar) return;
   lastBar=bar;
   Evaluate(bar);
}

void OnDeinit(const int reason)
{
   if(fastHandle!=INVALID_HANDLE) IndicatorRelease(fastHandle);
   if(slowHandle!=INVALID_HANDLE) IndicatorRelease(slowHandle);
   if(rsiHandle!=INVALID_HANDLE) IndicatorRelease(rsiHandle);
   if(atrHandle!=INVALID_HANDLE) IndicatorRelease(atrHandle);
   Comment("");
}
