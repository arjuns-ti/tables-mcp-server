#!/bin/sh

# Setup script for the Task MCP Server
echo "Setting up Tables MCP Server..." >&2

# Install dependencies (if needed)
echo "Installing dependencies..." >&2
uv sync > /dev/null 2>&1

# Build project (if needed)
echo "Building the project..." >&2
uv pip install -e . > /dev/null 2>&1

# Setup OAuth2 configuration if platform variables are available
if [ -n "$API_KEY" ] && [ -n "$API_BASE_URL" ] && [ -n "$HIVE_INSTANCE_ID" ]; then
    echo "Fetching OAuth2 configuration..." >&2
    
    if curl -s -X GET "$API_BASE_URL/api/hive-instances/$HIVE_INSTANCE_ID/oauth2-config" \
        -H "x-api-key: $API_KEY" > oauth_response.json 2>&1; then
        
        # Save credentials in the format expected by our MCP server
        echo "Configuring OAuth2 credentials..." >&2
        
        # Convert to Google OAuth2 authorized user format
        jq '{
          "client_id": .oauthKeys.client_id,
          "client_secret": .oauthKeys.client_secret,
          "refresh_token": .credentials.refresh_token,
          "token": .credentials.access_token,
          "type": "authorized_user"
        }' oauth_response.json > ./.gcp-saved-tokens.json
        
        # Create client secrets in Google OAuth format  
        mkdir -p ./credentials
        jq '{"web": .oauthKeys}' oauth_response.json > credentials/client_secrets.json

        echo "OAuth2 credentials configured successfully" >&2
        echo "Credentials:" >&2
        cat credentials/client_secrets.json >&2
        echo "Tokens:" >&2
        cat token.json >&2
        
        rm oauth_response.json
    else
        echo "OAuth2 configuration fetch failed, will use manual setup" >&2
    fi
fi

echo "Setup complete" >&2

# Output final JSON configuration to stdout (MANDATORY)
cat << EOF
{
  "command": "uv",
  "args": ["run", "main.py"],
  "env": {
    "GOOGLE_CLIENT_CONFIG": "./credentials/client_secrets.json",
    "GOOGLE_TOKEN_FILE": "./.gcp-saved-tokens.json",
    "DOWNLOAD_DIRECTORY": "./downloads",
    "MAX_FILE_SIZE_MB": "100"
  },
  "cwd": "$(pwd)"
}
EOF
