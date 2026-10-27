import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { CreateScalp } from './CreateScalp';
import { createBot } from '../lib/api';

const fixture = vi.hoisted(() => ({ now: 120000, scanned:120, ready:false }));
vi.mock('../hooks/useNow', () => ({useNow: () => fixture.now}));
vi.mock('../lib/api', () => ({createBot:vi.fn()}));
vi.mock('../hooks/useScan', () => ({
 useStrategies: () => ({ data:{default:'ema-trend-pullback',strategies:[{id:'ema-trend-pullback',name:'EMA',assumptions:[]}]}}),
 useScan: () => ({isError:false,isFetching:false,refetch:vi.fn(),data:{ready:true,error:null,
  scanned_at:fixture.scanned,strategy:{id:'ema-trend-pullback',name:'EMA',assumptions:[]},
  stats:{},rejections:[],candidates:[{symbol:'SOLUSDT',setup_ready:fixture.ready,
  setup_note:'No breakout confirmation',expires_at:100,regime:'trend',price:100,
  atr_pct:.5,spread_bps:1,stop_distance_pct:.5,cost_to_stop:.2,breakeven_win_rate:.4,
  quote_volume_24h:1e9,score:1}]}}),
}));
beforeEach(() => {vi.clearAllMocks();fixture.scanned=120;fixture.ready=false;});
function mount(){
 render(<QueryClientProvider client={new QueryClient()}><CreateScalp engineConnected tradingEnabled
 credentials={{configured:true,valid:true,can_trade:true,message:'ok',environment:'testnet',key_preview:'',balance:'1000'}} />
 </QueryClientProvider>);
}
it('selects a waiting symbol but requires explicit continuous mode before creating',async()=>{
 vi.mocked(createBot).mockResolvedValue(undefined);
 mount();
 expect(screen.getByText('No breakout confirmation')).toBeInTheDocument();
 const checkbox=screen.getByRole('checkbox',{name:'Select SOLUSDT'});
 expect(checkbox).toBeEnabled();fireEvent.click(checkbox);
 const create=screen.getByRole('button',{name:'Create bot with 1 symbol'});
 expect(create).toBeDisabled();
 fireEvent.click(screen.getByRole('checkbox',{name:/Continuous mode/}));
 expect(create).toBeEnabled();fireEvent.click(create);
 await waitFor(()=>expect(createBot).toHaveBeenCalledWith(expect.objectContaining({continuous:true,symbols:['SOLUSDT']})));
});
it('keeps stale scans unselectable',()=>{
 fixture.scanned=1;mount();
 expect(screen.getByRole('checkbox',{name:'Select SOLUSDT'})).toBeDisabled();
});
it('disables leverage values above the account cap',()=>{
 mount();
 expect(screen.getByRole('option',{name:'20x'})).toBeDisabled();
 expect(screen.getByRole('option',{name:'10x'})).toBeEnabled();
});
it('expires a previously ready signal without enabling immediate entry',()=>{
 fixture.ready=true;mount();
 expect(screen.getByText('Signal expired; wait for a new close')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('checkbox',{name:'Select SOLUSDT'}));
 expect(screen.getByRole('button',{name:'Create bot with 1 symbol'})).toBeDisabled();
});

it('prefills the favorite pair without submitting and keeps signal checks', async () => {
 render(<QueryClientProvider client={new QueryClient()}><CreateScalp engineConnected tradingEnabled
 initialPair={{symbol:'SOLUSDT',strategy:'ema-trend-pullback'}}
 credentials={{configured:true,valid:true,can_trade:true,message:'ok',environment:'testnet',key_preview:'',balance:'1000'}} />
 </QueryClientProvider>);
 expect(screen.getByLabelText('Bot type')).toHaveValue('ema-trend-pullback');
 expect(screen.getByRole('checkbox',{name:'Select SOLUSDT'})).toBeChecked();
 expect(createBot).not.toHaveBeenCalled();
 const create=screen.getByRole('button',{name:'Create bot with 1 symbol'});
 expect(create).toBeDisabled();
 fireEvent.click(screen.getByRole('checkbox',{name:/Continuous mode/}));
 fireEvent.click(create);
 await waitFor(()=>expect(createBot).toHaveBeenCalledWith(expect.objectContaining({strategy:'ema-trend-pullback',symbols:['SOLUSDT']})));
});

it('keeps an excluded favorite blocked rather than selecting a different coin', () => {
 render(<QueryClientProvider client={new QueryClient()}><CreateScalp engineConnected tradingEnabled
 initialPair={{symbol:'BTCUSDT',strategy:'ema-trend-pullback'}}
 credentials={{configured:true,valid:true,can_trade:true,message:'ok',environment:'testnet',key_preview:'',balance:'1000'}} />
 </QueryClientProvider>);
 expect(screen.getByText(/This coin is not eligible in the current scan/)).toBeInTheDocument();
 fireEvent.click(screen.getByRole('checkbox',{name:/Continuous mode/}));
 expect(screen.getByRole('button',{name:'Create bot with 1 symbol'})).toBeDisabled();
 expect(screen.getByRole('checkbox',{name:'Select SOLUSDT'})).not.toBeChecked();
 expect(createBot).not.toHaveBeenCalled();
});
