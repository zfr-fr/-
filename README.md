# 企业级软件开发-新闻管理系统

## Troubleshooting

### JsonReaderException: Error reading double. Unexpected token: Boolean

If you hit this error at:

```csharp
object value = info.Value.ToObject(setting.ValueType);
valueToIndexMap[value.ToString()] = index;
```

it means the JSON token is a Boolean, but you are deserializing into
`double` (or `double?`). Fix it by either correcting the JSON schema to
send numeric values, or handling Boolean tokens explicitly before calling
`ToObject`.

```csharp
// Example: convert boolean to 1/0 when a double is expected.
JToken token = info.Value;
object value;

if (setting.ValueType == typeof(double) || setting.ValueType == typeof(double?))
{
    if (token.Type == JTokenType.Boolean)
    {
        value = token.Value<bool>() ? 1d : 0d;
    }
    else if (token.Type == JTokenType.Integer || token.Type == JTokenType.Float)
    {
        value = token.Value<double>();
    }
    else if (token.Type == JTokenType.String &&
             double.TryParse(
                 token.ToString(),
                 NumberStyles.Any,
                 CultureInfo.InvariantCulture,
                 out double parsed))
    {
        value = parsed;
    }
    else
    {
        throw new JsonReaderException($"Expected number, got {token.Type}");
    }
}
else
{
    value = token.ToObject(setting.ValueType);
}

valueToIndexMap[value?.ToString() ?? string.Empty] = index;
```

Notes:
- If you do not want to coerce `bool` to `double`, reject it early and
  report a validation error.
- Ensure the JSON producer uses the correct types for the schema.
