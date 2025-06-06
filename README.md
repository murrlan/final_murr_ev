#stock shorting program
### Description:
stock shorting algorithm using alpaca api paper (fake money) trading

this program automatically identifies and shorts top-gaining stocks that meet specific criteria:

1. scrapes daily top gainers from yahoo finance
2. checks for merger/acquisition/ipo news using google news
3. verifies tradability through alpaca api
4. filters stocks based on user entered market capitalization
5. executes short orders with user confirmation through alpaca api
6. maintains a database of all trades
for future use this program can be used with real money trading, as alpaca allows you to link a broker and interact with it through their api
