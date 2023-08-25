import os
import ccxt
import cmd
import sys
import json
import threading
import time
import logging

def load_config_from_json(json_path):
    with open(json_path, 'r') as json_file:
        config_data = json.load(json_file)
    return config_data

class TradingTool(cmd.Cmd):
    intro = "Welcome to the Trading Tool. Type 'help' for a list of commands."
    prompt = "[BTCUSDT] > "

    def __init__(self,config):
        super().__init__()
        self.instrument = "BTCUSDT"
        self.alias = {}
        # Initialize the logging
        self.initialize_logging()

        # Define individual clients
        self.clients={}
        for client_name, client_config in config['clients'].items():
            for key, value in client_config.items():
                if isinstance(value, str) and value.startswith('${') and value.endswith('}'):
                    env_var_name = value[2:-1]
                    env_var_value = os.environ.get(env_var_name)
                    if env_var_value is not None:
                        config['clients'][client_name][key] = env_var_value
            if client_config['exchange'] == 'binance':
                self.clients[client_name] = ccxt.binance(client_config)
            elif client_config['exchange'] == 'okex':
                self.clients[client_name] = ccxt.okex5(client_config)
            # Add more conditions for other exchanges if needed
        self.groups = {}
        for group_name, group_clients in config['groups'].items():
            self.groups[group_name] = [self.clients[client_name] for client_name in group_clients]


        self.current_targets = None  # Will hold either a list of clients (for a group) or a single client

        self.load_aliases('aliases.txt')

         # Background login thread
        self.background_login_thread = threading.Thread(target=self.background_login)
        self.background_login_thread.daemon = True
        self.background_login_thread.start()

    def do_login(self, args):
        """Login to a specific client or group. Example: login copyfuture"""
        target = args.strip()
        self.current_targets = None  # Reset the current_targets to avoid previous state issues.

        # Log in to a group
        if target in self.groups:
            self.current_targets = [(name, client) for name, client in self.clients.items() if
                                    client in self.groups[target]]
            print(f"Logged in to group {target}")

        # Log in to a single client
        elif target in self.clients:
            self.current_targets = [(target, self.clients[target])]
            print(f"Logged in to client {target}")

        else:
            print(
                f"Invalid target. Available clients and groups: {', '.join(list(self.clients.keys()) + list(self.groups.keys()))}")
            return  # Return here to avoid the rest of the code

        # The following code will now only execute if current_targets is not None
        try:
            for name, target in self.current_targets:
                balance = target.fetch_balance()
                print(f"({name}) {balance['USDT']['free']}")
        except Exception as e:
            print(e)

    
    def initialize_logging(self):
        logging.basicConfig(
            filename='login.log',
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            filemode='w'  # Set filemode to 'w' for write mode

        )
        self.logger = logging.getLogger('TradingTool')

    def background_login(self):
        while True:
            self.logger.info("Performing background login to all clients and groups...")

            for client_name, client in self.clients.items():
                self.logger.info(f"Logging in to client: {client_name}")
                try:
                    client.load_markets()  # Load markets to avoid rate limits
                    client.fetch_balance()  # Fetch balance to perform login
                except Exception as e:
                    self.logger.error(f"Error logging in to client {client_name}: {str(e)}")

            for group_name, group_clients in self.groups.items():
                for client in group_clients:
                    client_name = next(name for name, c in self.clients.items() if c == client)
                    self.logger.info(f"Logging in to group client: {client_name}")
                    try:
                        client.load_markets()  # Load markets to avoid rate limits
                        client.fetch_balance()  # Fetch balance to perform login
                    except Exception as e:
                        self.logger.error(f"Error logging in to group client {client_name}: {str(e)}")

            self.logger.info("Background login completed. Sleeping for 1 minutes...")
            time.sleep(60)  # Sleep for 5 minutes before re-logging in

    def load_aliases(self, filename):
        try:
            with open(filename, 'r') as file:
                for line in file:
                    parts = line.strip().split(' ', 2)
                    if len(parts) == 3 and parts[0] == 'alias':
                        alias, command = parts[1], parts[2]
                        self.alias[alias] = command
            print(f"Loaded {len(self.alias)} aliases.")
        except FileNotFoundError:
            print(f"File {filename} not found. No aliases were loaded.")

    def precmd(self, line):
        line = line.strip()
        command = self.alias.get(line)
        return command if command else line

    def do_instrument(self, args):
        """Select a trading instrument. Example: instrument btcusdt"""
        self.instrument = args
        self.prompt = f"[{self.instrument.upper()}] > "

    def do_leverage(self, args):
        if self.current_targets is None:
            print("Please login to a client or group first.")
            return
        """Set the leverage for the selected instrument. Example: leverage 20"""
        try:
            leverage = int(args)
            for name, target in self.current_targets:
                if isinstance(target, ccxt.okex5):
                    modified_instrument = self.instrument.replace("USDT", "-USDT")  # Convert to BTC-USDT format
                else:
                    modified_instrument = self.instrument
                target.set_leverage(leverage, modified_instrument)
                print(f"({name})Leverage set to {leverage} for {modified_instrument}")
        except ValueError:
            print("Invalid leverage value")

    def do_buy(self, args):
        """Place a buy order. Example: buy 1 or buy 1 29000 or buy 100%"""
        self.place_order_from_percentage("buy", args)

    def do_sell(self, args):
        """Place a sell order. Example: sell 1 or sell 1 29000 or sell 100%"""
        self.place_order_from_percentage("sell", args)

    def place_order_from_percentage(self, side, args):
        if self.current_targets is None:
            print("Please login to a client or group first.")
            return

        parts = args.split()
        try:
            amount_arg = parts[0]
            price_arg = float(parts[1]) if len(parts) > 1 else None

            for name, target in self.current_targets:
                balance = target.fetch_balance()
                usdt_balance = balance['USDT']['free']
                # Determine the appropriate instrument symbol based on the exchange
                if isinstance(target, ccxt.okex5):
                    modified_instrument = self.instrument.replace("USDT", "-USDT")  # Convert to BTC-USDT format
                else:
                    modified_instrument = self.instrument  # Keep the original format

                price = target.fetch_ticker(modified_instrument)['last']

                if "%" in amount_arg:
                    percentage = float(amount_arg.strip('%')) / 100
                    amount = (usdt_balance * percentage) / price
                else:
                    amount = float(amount_arg)

                if price_arg:
                    self.place_order((name, target), side, amount, price_arg)
                else:
                    self.place_order((name, target), side, amount)

        except Exception as e:
            print(f"Error: {str(e)}")

    def do_close(self, args):
        try:

            if self.current_targets is None:
                print("Please login to a client or group first.")
                return
            """Close the position. Example: close current or close all"""
            for name, target in self.current_targets:
                positions = target.fetch_positions()

                if args == "current":
                    if isinstance(target, ccxt.okex5):
                        modified_instrument = self.instrument.replace("USDT", "-USDT")  # Convert to BTC-USDT format
                    else:
                        modified_instrument = self.instrument  # Keep the original format

                    self.close_positions([modified_instrument])
                elif args == "all":
                    positions = target.fetch_positions()
                    symbols = set(
                        pos['info']['symbol'].replace("/", "") for pos in positions if
                        float(pos['info']['positionAmt']) != 0)
                    self.close_positions(symbols)
                else:
                    print("Invalid argument. Use 'close current' or 'close all'.")
        except Exception as e:
            print(f"Error: {str(e)}")

    def close_positions(self, symbols):
        try:

            if self.current_targets is None:
                print("Please login to a client or group first.")
                return
            for name, target in self.current_targets:
                for symbol in symbols:
                    positions = target.fetch_positions()
                    for pos in positions:
                        if pos['info']['symbol'].replace("/", "") == symbol and float(pos['info']['positionAmt']) != 0:
                            side = 'sell' if pos['side'] == 'long' else 'buy'
                            quantity = abs(float(pos['info']['positionAmt']))
                            target.create_market_order(symbol.replace("/", ""), side, quantity)
                            print(f"({name})Closed position for {symbol}")
        except Exception as e :
            print(f"Error: {str(e)}")

    def place_order(self, target, side, amount, price=None):
        name, target = target

        try:
            if price:
                if isinstance(target, ccxt.okex5):
                    modified_instrument = self.instrument.replace("USDT", "-USDT")  # Convert to BTC-USDT format
                else:
                    modified_instrument = self.instrument  # Keep the original format
                target.create_limit_order(modified_instrument, side, amount, price)
                print(f"({name})Placed a {side} limit order for {amount} {modified_instrument} at {price}")
            else:
                if isinstance(target, ccxt.okex5):
                    modified_instrument = self.instrument.replace("USDT", "-USDT")  # Convert to BTC-USDT format
                else:
                    modified_instrument = self.instrument  # Keep the original format
                target.create_market_order(modified_instrument, side, amount)
                print(f"({name})Placed a {side} market order for {amount} {modified_instrument}")
        except Exception as e:
            print(f"({name})Error placing order: {str(e)}")

    def do_positions(self, args):
        """List the current positions with PnL"""
        self.display_positions()

    def display_positions(self):            
        if self.current_targets is None:
            print("Please login to a client or group first.")
            return
        try:
            for name, target in self.current_targets:
                positions = target.fetch_positions()
                for pos in positions:
                    symbol = pos['info']['symbol'].replace("/", "")
                    quantity = float(pos['info']['positionAmt'])
                    if quantity != 0:
                        side = 'long' if quantity > 0 else 'short'
                        entry_price = float(pos['info']['entryPrice'])
                        current_price = float(target.fetch_ticker(symbol)['last'])
                        pnl = (current_price - entry_price) * abs(quantity)
                        pnl = pnl if side == 'long' else -pnl
                        print(
                            f"({name}){side} {symbol} quantity {quantity:.4f} from {entry_price:.2f} with pnl ${pnl:.2f}")
        except Exception as e:
            print(f"Error: {str(e)}")

    # Alias for positions
    def do_pl(self, args):
        """Alias for 'positions' command"""
        self.display_positions()

    def do_order_list(self, args):
        if self.current_targets is None:
            print("Please login to a client or group first.")
            return
        """List all the open orders for the current symbol"""
        try:
            for name, target in self.current_targets:
                if isinstance(target, ccxt.okex5):
                    modified_instrument = self.instrument.replace("USDT", "-USDT")  # Convert to BTC-USDT format
                else:
                    modified_instrument = self.instrument
                open_orders = target.fetch_open_orders(modified_instrument)  # Fetch orders for the current symbol
                if not open_orders:
                    print(f"({name})No open orders for {modified_instrument}")
                    return

                for order in open_orders:
                    side = order['side']
                    type_ = order['type']
                    symbol = order['symbol'].replace("/", "")
                    quantity = order['amount']
                    if type_ == 'limit':
                        price = order['price']  # Using 'price' for limit orders
                    else:
                        price = order.get('triggerPrice', order['info'].get('stopPrice',
                                                                            'N/A'))  # Using 'triggerPrice' or 'stopPrice' for other orders
                    if price is None:
                        price = 'N/A'

                    print(f"({name}){type_} {side} for {symbol} of {quantity:.4f} quantity at {price:.2f}")
        except Exception as e:
            print(f"Error: {str(e)}")

    def do_cancel_order(self, args):            
        if self.current_targets is None:
            print("Please login to a client or group first.")
            return
        """Cancel all open orders for the current symbol, including stop market orders"""
        try:
            for name, target in self.current_targets:
                if isinstance(target, ccxt.okex5):
                    modified_instrument = self.instrument.replace("USDT", "-USDT")  # Convert to BTC-USDT format
                else:
                    modified_instrument = self.instrument  # Keep the original format
                open_orders = target.fetch_open_orders(modified_instrument)
                for order in open_orders:
                    if isinstance(target, ccxt.okex5):
                        modified_instrument = self.instrument.replace("USDT", "-USDT")  # Convert to BTC-USDT format
                    else:
                        modified_instrument = self.instrument  # Keep the original format
                    target.cancel_order(order['id'], modified_instrument)
                print(f"({name})All open orders for {modified_instrument} have been cancelled.")
        except Exception as e:
            print(f"Error: {str(e)}")

    def do_balance(self, args):
        try:

            """Show balances that are greater than 0."""
            if self.current_targets is None:
                print("Please login to a client or group first.")
                return

            if args.lower() == "list":
                for name, target in self.current_targets:
                    balance = target.fetch_balance()
                    for currency, details in balance['total'].items():
                        if details > 0:
                            print(f"({name}){currency} balance: {details}")
        except Exception as e:
            print(e)

    def do_stop(self, args):            
        if self.current_targets is None:
            print("Please login to a client or group first.")
            return
        """Set a stop market order for the current position. Example: stop 28000"""
        try:
            for name, target in self.current_targets:
                # Fetching current position
                positions = target.fetch_positions()
                if isinstance(target, ccxt.okex5):
                    modified_instrument = self.instrument.replace("USDT", "-USDT")  # Convert to BTC-USDT format
                else:
                    modified_instrument = self.instrument  # Keep the original format
                position = next(
                    (p for p in positions if p['info']['symbol'].replace("/", "") == modified_instrument and float(
                        p['info']['positionAmt']) != 0), None)

                if position is None:
                    print(f"({name})No open position found for {modified_instrument}")
                    return

                quantity = abs(float(position['info']['positionAmt']))
                stop_price = float(args)
                side = 'sell' if float(position['info']['positionAmt']) > 0 else 'buy'

                if side == 'sell':
                    params = {
                        'stopPrice': stop_price,
                    }
                    target.create_order(symbol=modified_instrument, type="market", amount=quantity, side="sell",
                                        params=params)
                else:
                    params = {
                        'stopPrice': stop_price,
                    }
                    target.create_order(symbol=modified_instrument, type="market", amount=quantity, side="buy",
                                        params=params)

                print(f"({name})Stop market order set for {modified_instrument}: {side} {quantity} at {stop_price}")
        except Exception as e:
            print(f"Error: {str(e)}")

    def do_exit(self, args):
        """Exit the trading tool."""
        print("Exiting...")
        sys.exit()


if __name__ == '__main__':
    json_path = 'configs.json'  
    config = load_config_from_json(json_path)
    tool = TradingTool(config)
    tool.cmdloop()
