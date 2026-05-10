# User Configuration Files

Place one YAML file per user here named `{username}.yaml`.

## Example: `admin.yaml`

```yaml
binance:
  api_key: "your-api-key-here"
  api_secret: "your-api-secret-here"
  testnet: false      # set to true to use Binance Testnet

trading:
  mock_mode: false    # set to true to simulate orders (no real API calls)
```

## Notes

- Users can edit their own config file directly, or use the **Settings** tab in the app.
- `mock_mode: true` is recommended for testing – orders are simulated without touching Binance.
- `testnet: true` uses Binance Testnet (https://testnet.binance.vision).
- Never commit real API keys to version control.
