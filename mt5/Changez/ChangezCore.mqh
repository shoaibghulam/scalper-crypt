//+------------------------------------------------------------------+
//|                                             ICT_Confluence_EA.mq5|
//|                                  Converted from TradingView Pine |
//|                     Mod: 1 Trade Max, Scaled Account-Tier Risk   |
//+------------------------------------------------------------------+




#include <Trade\Trade.mqh>

// ==========================================
// 1. INPUT PARAMETERS
// ==========================================
input bool EnableEntries = false; // Explicitly enable in demo/tester
input double RiskCapPercent = 1.0; // Cap original account-tier risk
input int BrokerUTCOffsetHours = 99; // Required when sessions enabled; 99 = unset
input int MaxSpreadPoints = 20;
input group "Time Architecture"
input bool   useTimeFilter = true;        // Enforce Killzone Filter
input string londonSession = "0200-0500"; // London Killzone (NY Time)
input string nySession     = "0830-1100"; // New York Open Killzone (NY Time)

input group "Displacement"
input int    vShapeLength  = 5;           // Displacement Base SMA Length
input double vShapeMult    = 1.5;         // Displacement Body Multiplier

input group "Trade Management"
input double InpRiskReward    = 5.0;      // Risk:Reward Ratio (e.g., 5 for 1:5 RR)
input ulong  InpMagicNumber   = CHANGEZ_MAGIC;   // Magic Number
input ulong  InpSlippage      = 3;        // Slippage (Points)


// --- Global Variables ---
CTrade   trade;
double   lastSwingHigh = EMPTY_VALUE;
double   lastSwingLow  = EMPTY_VALUE;
datetime lastBarTime   = 0;
bool pivotsReady=false;
bool entryUncertain=false;
string changezMessage="Waiting for next closed candle";

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
   if(!DemoAllowed()) { Print("Changez: demo accounts or Strategy Tester only"); return INIT_FAILED; }
   if(vShapeLength<2 || vShapeLength>500 || vShapeMult<=0 || InpRiskReward<=0 ||
      RiskCapPercent<=0 || RiskCapPercent>5 || MaxSpreadPoints<1 || InpMagicNumber==0 ||
      (useTimeFilter && (BrokerUTCOffsetHours < -12 || BrokerUTCOffsetHours > 14 ||
       !ValidSession(londonSession) || !ValidSession(nySession))))
     { Print("Changez: invalid inputs. Set broker UTC offset for session filtering."); return INIT_PARAMETERS_INCORRECT; }
   trade.SetAsyncMode(false);
   if(!trade.SetTypeFillingBySymbol(_Symbol)) return INIT_FAILED;
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippage);
   
   lastBarTime = iTime(Symbol(), CHANGEZ_TIMEFRAME, 0);
   
   // Pre-fill lastSwingHigh and lastSwingLow based on historical bars
   InitPivots();
   
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
  {
   if(!DemoAllowed()) return;
   Comment(CHANGEZ_NAME," | ",_Symbol," | Entries ",EnableEntries ? "ON" : "OFF",
           "\nICT MSS + premium/discount + FVG | Risk cap ",RiskCapPercent,"%",
           "\n",changezMessage);
   if(iBars(_Symbol,CHANGEZ_TIMEFRAME)<MathMax(105,vShapeLength+10)) return;
   if(!pivotsReady) { InitPivots(); pivotsReady=true; }

   // Check if a new bar just opened. Trade logic runs once per closed bar.
   datetime currentBarTime = iTime(Symbol(), CHANGEZ_TIMEFRAME, 0);
   if(currentBarTime > lastBarTime)
     {
      lastBarTime = currentBarTime;
      
      // 1. Update Pivot Points based on the newly closed bar (Shift 1)
      UpdatePivots();
      
      // 2. Evaluate Signals on the closed bar
      CheckSignals();
     }
  }

//+------------------------------------------------------------------+
//| SIGNAL LOGIC (Evaluated on Closed Bar - Shift 1)                 |
//+------------------------------------------------------------------+
void CheckSignals()
  {
   // RULE CHECK: Ensure no more than ONE trade is currently open for this EA
   if(!EnableEntries) { changezMessage="Monitoring only: EnableEntries=false"; return; }
   if(entryUncertain) { changezMessage="Unconfirmed order: inspect account before reattaching"; return; }
   // Block any symbol position/order, including the other Changez timeframe.
   if(PositionSelect(_Symbol)) { changezMessage="Symbol already has a position"; return; }
   for(int n=0;n<OrdersTotal();n++)
      if(OrderGetTicket(n)>0 && OrderGetString(ORDER_SYMBOL)==_Symbol) return;
   changezMessage="Waiting for MSS + zone + FVG + session confluence";

   // Ensure we have enough bars to process 100-bar Premium/Discount Arrays
   if(iBars(Symbol(), CHANGEZ_TIMEFRAME) < 100) return;

   // --- 1. TIME ARCHITECTURE (NY TIME) ---
   datetime barOpenTimeBroker = iTime(Symbol(), CHANGEZ_TIMEFRAME, 1);
   datetime nyTime = GetNYTime(barOpenTimeBroker);
   
   bool isLondon = IsTimeInSession(nyTime, londonSession);
   bool isNY     = IsTimeInSession(nyTime, nySession);
   bool allowExecution = (!useTimeFilter) || isLondon || isNY;

   // --- 2. MARKET STRUCTURE SHIFT (MSS) ---
   double close_1 = iClose(Symbol(), CHANGEZ_TIMEFRAME, 1);
   double close_2 = iClose(Symbol(), CHANGEZ_TIMEFRAME, 2);
   
   bool bullishMSS = (lastSwingHigh != EMPTY_VALUE) && (close_1 > lastSwingHigh) && (close_2 <= lastSwingHigh);
   bool bearishMSS = (lastSwingLow != EMPTY_VALUE)  && (close_1 < lastSwingLow)  && (close_2 >= lastSwingLow);

   // --- 3. PREMIUM / DISCOUNT ---
   int highest_100_idx = iHighest(Symbol(), CHANGEZ_TIMEFRAME, MODE_HIGH, 100, 1);
   int lowest_100_idx  = iLowest(Symbol(), CHANGEZ_TIMEFRAME, MODE_LOW, 100, 1);
   double rangeHigh    = iHigh(Symbol(), CHANGEZ_TIMEFRAME, highest_100_idx);
   double rangeLow     = iLow(Symbol(), CHANGEZ_TIMEFRAME, lowest_100_idx);
   
   double equilibrium = (rangeHigh + rangeLow) / 2.0;
   bool inPremium  = (close_1 > equilibrium);
   bool inDiscount = (close_1 < equilibrium);

   // --- 4. LOCAL HIGHS / LOWS (FOR STOP LOSS) ---
   int highest_10_idx = iHighest(Symbol(), CHANGEZ_TIMEFRAME, MODE_HIGH, 10, 1);
   int lowest_10_idx  = iLowest(Symbol(), CHANGEZ_TIMEFRAME, MODE_LOW, 10, 1);
   double localHigh   = iHigh(Symbol(), CHANGEZ_TIMEFRAME, highest_10_idx);
   double localLow    = iLow(Symbol(), CHANGEZ_TIMEFRAME, lowest_10_idx);

   // --- 5. FVG DETECTION ---
   bool fvgBull = FvgBullDetected(1) || FvgBullDetected(2); 
   bool fvgBear = FvgBearDetected(1) || FvgBearDetected(2); 

   // --- 6. UNIFIED CONFLUENCE SIGNALS ---
   bool institutionalLong  = bullishMSS && inDiscount && allowExecution && fvgBull;
   bool institutionalShort = bearishMSS && inPremium  && allowExecution && fvgBear;

   // --- 7. TRADE EXECUTION WITH TIERED ACCOUNT RISK & RR ---
   if(institutionalLong)
     {

      
      double ask = SymbolInfoDouble(Symbol(), SYMBOL_ASK);
      double sl  = NormalizeDouble(localLow, _Digits);
      
      if(ask > sl)
        {
         double slDistance = ask - sl;
         double tp         = NormalizeDouble(ask + (slDistance * InpRiskReward), _Digits);
         double lotSize    = CalculateTieredLotSize(ask, sl);
         
         if(lotSize > 0)
            SendChangez(ORDER_TYPE_BUY,lotSize,ask,sl,tp);
        }
     }
   else if(institutionalShort)
     {

      
      double bid = SymbolInfoDouble(Symbol(), SYMBOL_BID);
      double sl  = NormalizeDouble(localHigh, _Digits);
      
      if(bid < sl)
        {
         double slDistance = sl - bid;
         double tp         = NormalizeDouble(bid - (slDistance * InpRiskReward), _Digits);
         double lotSize    = CalculateTieredLotSize(bid, sl);
         
         if(lotSize > 0)
            SendChangez(ORDER_TYPE_SELL,lotSize,bid,sl,tp);
        }
     }
  }

//+------------------------------------------------------------------+
//| TIERED RISK MANAGEMENT LOT SIZE CALCULATOR                       |
//+------------------------------------------------------------------+
double CalculateTieredLotSize(double entryPrice, double stopLoss)
  {
   // Determine Account Equity / Balance Tier
   double currentBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   double targetRiskAmount = 30.0; // Default tier (< $1000 balance) -> $30 risk

   if(currentBalance >= 1500.0)
      targetRiskAmount = 120.0;
   else if(currentBalance >= 1000.0)
      targetRiskAmount = 80.0;

   targetRiskAmount=MathMin(targetRiskAmount,AccountInfoDouble(ACCOUNT_EQUITY)*RiskCapPercent/100);
   double minLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double maxLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double stepLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   double loss=0;
   ENUM_ORDER_TYPE side=entryPrice>stopLoss ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   if(minLot<=0 || stepLot<=0 || targetRiskAmount<=0 ||
      !OrderCalcProfit(side,_Symbol,minLot,entryPrice,stopLoss,loss) || loss>=0) return 0;
   double rawLotSize=targetRiskAmount/MathAbs(loss)*minLot;
   double finalLotSize=NormalizeDouble(MathFloor(MathMin(rawLotSize,maxLot)/stepLot)*stepLot,8);
   if(finalLotSize<minLot) { changezMessage="Minimum lot exceeds risk allowance"; return 0; }
   return finalLotSize;
  }

//+------------------------------------------------------------------+
//| CHECK ACTIVE TRADES COUNT                                        |
//+------------------------------------------------------------------+
int CountOpenPositions()
  {
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(PositionGetSymbol(i) == Symbol() && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
        {
         count++;
        }
     }
   return count;
  }

//+------------------------------------------------------------------+
//| FVG & DISPLACEMENT HELPER FUNCTIONS                              |
//+------------------------------------------------------------------+
bool IsDisplaced(int shift)
  {
   double avg = 0;
   for(int i = 0; i < vShapeLength; i++)
     {
      double o = iOpen(Symbol(), CHANGEZ_TIMEFRAME, shift + i);
      double c = iClose(Symbol(), CHANGEZ_TIMEFRAME, shift + i);
      avg += MathAbs(c - o);
     }
   avg /= (double)vShapeLength;
   
   double targetOpen  = iOpen(Symbol(), CHANGEZ_TIMEFRAME, shift);
   double targetClose = iClose(Symbol(), CHANGEZ_TIMEFRAME, shift);
   double body = MathAbs(targetClose - targetOpen);
   
   return (body > (avg * vShapeMult));
  }

bool FvgBullDetected(int shift)
  {
   double low_0   = iLow(Symbol(), CHANGEZ_TIMEFRAME, shift);
   double high_2  = iHigh(Symbol(), CHANGEZ_TIMEFRAME, shift + 2);
   double close_1 = iClose(Symbol(), CHANGEZ_TIMEFRAME, shift + 1);
   bool disp_1    = IsDisplaced(shift + 1);
   
   return (low_0 > high_2) && (close_1 > high_2) && disp_1;
  }

bool FvgBearDetected(int shift)
  {
   double high_0  = iHigh(Symbol(), CHANGEZ_TIMEFRAME, shift);
   double low_2   = iLow(Symbol(), CHANGEZ_TIMEFRAME, shift + 2);
   double close_1 = iClose(Symbol(), CHANGEZ_TIMEFRAME, shift + 1);
   bool disp_1    = IsDisplaced(shift + 1);
   
   return (high_0 < low_2) && (close_1 < low_2) && disp_1;
  }

//+------------------------------------------------------------------+
//| PIVOT MANAGEMENT                                                 |
//+------------------------------------------------------------------+
void UpdatePivots()
  {
   double h0 = iHigh(Symbol(), CHANGEZ_TIMEFRAME, 1);
   double h1 = iHigh(Symbol(), CHANGEZ_TIMEFRAME, 2);
   double h2 = iHigh(Symbol(), CHANGEZ_TIMEFRAME, 3); 
   double h3 = iHigh(Symbol(), CHANGEZ_TIMEFRAME, 4);
   double h4 = iHigh(Symbol(), CHANGEZ_TIMEFRAME, 5);
   
   if(h2 > h4 && h2 > h3 && h2 > h1 && h2 > h0)
      lastSwingHigh = h2;

   double l0 = iLow(Symbol(), CHANGEZ_TIMEFRAME, 1);
   double l1 = iLow(Symbol(), CHANGEZ_TIMEFRAME, 2);
   double l2 = iLow(Symbol(), CHANGEZ_TIMEFRAME, 3); 
   double l3 = iLow(Symbol(), CHANGEZ_TIMEFRAME, 4);
   double l4 = iLow(Symbol(), CHANGEZ_TIMEFRAME, 5);
   
   if(l2 < l4 && l2 < l3 && l2 < l1 && l2 < l0)
      lastSwingLow = l2;
  }

void InitPivots()
  {
   lastSwingHigh = EMPTY_VALUE;
   lastSwingLow  = EMPTY_VALUE;
   
   int bars = iBars(Symbol(), CHANGEZ_TIMEFRAME);
   int limit = (int)MathMin(bars - 1, 1000); 
   
   for(int i = limit; i >= 1; i--)
     {
      if(i + 4 >= bars) continue;
      
      double h0 = iHigh(Symbol(), CHANGEZ_TIMEFRAME, i);
      double h1 = iHigh(Symbol(), CHANGEZ_TIMEFRAME, i + 1);
      double h2 = iHigh(Symbol(), CHANGEZ_TIMEFRAME, i + 2);
      double h3 = iHigh(Symbol(), CHANGEZ_TIMEFRAME, i + 3);
      double h4 = iHigh(Symbol(), CHANGEZ_TIMEFRAME, i + 4);
      
      if(h2 > h4 && h2 > h3 && h2 > h1 && h2 > h0)
         lastSwingHigh = h2;

      double l0 = iLow(Symbol(), CHANGEZ_TIMEFRAME, i);
      double l1 = iLow(Symbol(), CHANGEZ_TIMEFRAME, i + 1);
      double l2 = iLow(Symbol(), CHANGEZ_TIMEFRAME, i + 2);
      double l3 = iLow(Symbol(), CHANGEZ_TIMEFRAME, i + 3);
      double l4 = iLow(Symbol(), CHANGEZ_TIMEFRAME, i + 4);
      
      if(l2 < l4 && l2 < l3 && l2 < l1 && l2 < l0)
         lastSwingLow = l2;
     }
  }

//+------------------------------------------------------------------+
//| TIME & SESSION MANAGEMENT                                        |
//+------------------------------------------------------------------+
datetime GetNYTime(datetime barOpenBrokerTime)
  {
   // Explicit historical broker offset; tester TimeGMT is not a reliable conversion.
   datetime barGMT=barOpenBrokerTime-BrokerUTCOffsetHours*3600;

   MqlDateTime tm;
   TimeToStruct(barGMT, tm);
   
   bool isUSDST = false;
   if(tm.mon > 3 && tm.mon < 11) isUSDST = true;
   else if(tm.mon == 3)
     {
      int firstDayOfWeek = (tm.day_of_week - (tm.day - 1) % 7 + 7) % 7;
      int secondSunday = (7 - firstDayOfWeek) % 7 + 8;
      if(tm.day > secondSunday || (tm.day == secondSunday && tm.hour >= 7)) isUSDST = true;
     }
   else if(tm.mon == 11)
     {
      int firstDayOfWeek = (tm.day_of_week - (tm.day - 1) % 7 + 7) % 7;
      int firstSunday = (7 - firstDayOfWeek) % 7 + 1;
      if(tm.day < firstSunday || (tm.day == firstSunday && tm.hour < 6)) isUSDST = true;
     }
     
   int nyOffsetHours = isUSDST ? -4 : -5;
   return barGMT + nyOffsetHours * 3600;
  }

bool IsTimeInSession(datetime nyTime, string session)
  {
   if(StringLen(session) < 9) return false;
   
   int startH = (int)StringToInteger(StringSubstr(session, 0, 2));
   int startM = (int)StringToInteger(StringSubstr(session, 2, 2));
   int endH   = (int)StringToInteger(StringSubstr(session, 5, 2));
   int endM   = (int)StringToInteger(StringSubstr(session, 7, 2));
   
   MqlDateTime tm;
   TimeToStruct(nyTime, tm);
   int currentMins = tm.hour * 60 + tm.min;
   int startMins   = startH * 60 + startM;
   int endMins     = endH * 60 + endM;
   
   if(startMins < endMins)
     {
      return (currentMins >= startMins && currentMins < endMins);
     }
   else 
     {
      return (currentMins >= startMins || currentMins < endMins);
     }
  }

//+------------------------------------------------------------------+
//| UTILITY FUNCTIONS                                                |
//+------------------------------------------------------------------+
void ClosePositions(ENUM_POSITION_TYPE type)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(PositionGetString(POSITION_SYMBOL) == Symbol() && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
        {
         if(PositionGetInteger(POSITION_TYPE) == type)
            trade.PositionClose(ticket);
        }
     }
  }
//+------------------------------------------------------------------+
// Shared execution checks for both fixed-timeframe wrappers.
bool DemoAllowed()
{
   return (bool)MQLInfoInteger(MQL_TESTER) || AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO;
}
bool ValidSession(string value)
{
   if(StringLen(value)!=9 || StringSubstr(value,4,1)!="-") return false;
   for(int i=0;i<9;i++)
      if(i!=4 && (StringGetCharacter(value,i)<48 || StringGetCharacter(value,i)>57)) return false;
   int sh=(int)StringToInteger(StringSubstr(value,0,2)),sm=(int)StringToInteger(StringSubstr(value,2,2));
   int eh=(int)StringToInteger(StringSubstr(value,5,2)),em=(int)StringToInteger(StringSubstr(value,7,2));
   return sh<24 && eh<24 && sm<60 && em<60 && sh*60+sm!=eh*60+em;
}
void SendChangez(ENUM_ORDER_TYPE side,double lots,double entry,double sl,double tp)
{
   if(!DemoAllowed() || !EnableEntries || !TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ||
      !MQLInfoInteger(MQL_TRADE_ALLOWED) || !AccountInfoInteger(ACCOUNT_TRADE_EXPERT)) return;
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0 || tick.ask<tick.bid ||
      TimeCurrent()-tick.time>10 || tick.ask-tick.bid>MaxSpreadPoints*_Point)
     { changezMessage="Spread or quote freshness check failed"; return; }
   double step=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(step<=0) return;
   bool buy=side==ORDER_TYPE_BUY;
   sl=NormalizeDouble((buy ? MathFloor(sl/step) : MathCeil(sl/step))*step,_Digits);
   entry=buy ? tick.ask : tick.bid;
   double distance=buy ? entry-sl : sl-entry;
   if(distance<=0) return;
   tp=entry+(buy ? 1 : -1)*distance*InpRiskReward;
   tp=NormalizeDouble((buy ? MathCeil(tp/step) : MathFloor(tp/step))*step,_Digits);
   double minimum=(SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)+1)*_Point;
   if(sl<=0 || tp<=0 || (buy && (tick.bid-sl<minimum || tp-tick.bid<minimum)) ||
      (!buy && (sl-tick.ask<minimum || tick.ask-tp<minimum)))
     { changezMessage="Local swing stop violates broker stop distance"; return; }
   lots=CalculateTieredLotSize(entry,sl);
   double margin=0;
   if(lots<=0 || !OrderCalcMargin(side,_Symbol,lots,entry,margin) ||
      margin>AccountInfoDouble(ACCOUNT_MARGIN_FREE)*0.8)
     { changezMessage="Lot risk or free-margin check failed"; return; }
   bool sent=trade.PositionOpen(_Symbol,side,lots,entry,sl,tp,CHANGEZ_NAME);
   uint rc=trade.ResultRetcode();
   if(!sent || (rc!=TRADE_RETCODE_DONE && rc!=TRADE_RETCODE_DONE_PARTIAL))
     { entryUncertain=true; changezMessage="Entry unconfirmed: "+trade.ResultRetcodeDescription(); }
   else changezMessage="Demo entry filled with SL/TP";
   Print(CHANGEZ_NAME,": ",changezMessage," retcode=",rc," deal=",trade.ResultDeal());
}
void OnDeinit(const int reason) { Comment(""); }
