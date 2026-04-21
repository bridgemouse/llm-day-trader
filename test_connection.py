# test_connection.py                                                                                                                                                                                                     
from alpaca.trading.client import TradingClient                                                                                                                                                                          
from dotenv import load_dotenv                                                                                                                                                                                           
import os                                                                                                                                                                                                                
                                                                                                                                                                                                                         
load_dotenv()                                                                                                                                                                                                            
                                                          
client = TradingClient(                                                                                                                                                                                                  
    os.getenv("ALPACA_API_KEY"),                          
    os.getenv("ALPACA_SECRET_KEY"),
    paper=True                              
)                                       

account = client.get_account()                                                                                                                                                                                           
print(f"Account status: {account.status}")
print(f"Buying power: ${account.buying_power}")                                                                                                                                                                          
print(f"Portfolio value: ${account.portfolio_value}") 