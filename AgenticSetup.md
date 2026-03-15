# Setup venv
    python3 -m venv agentic_env
    source agentic_env/bin/activate
	



# Setting Up the MCP Server
1. Install requirements

    pip install mcp==1.7.0 mcp[cli] uv==0.7.6 praisonaiagents==0.0.82 praisonaiagents[llm]

2. write agents in agents/mcp_server.py


# Install node

Download and install nvm:

    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash

in lieu of restarting the shell
    
    \. "$HOME/.nvm/nvm.sh"
    

Download and install Node.js:

    nvm install 24


Verify the Node.js version:

    node -v # Should print "v24.12.0".

Verify npm version:

    npm -v # Should print "11.6.2".


# Run the MCP server

3. Run the mcp server

    cd chat_ui && mcp dev agents/mcp_server.py
    
    
# LLM

The doc suggested "qwen2.5:3b" but seems expensive for my Mac, so I downloaded the older version

    ollama pull qwen2.5:3b 
    

# Running the agent

    python agents/agent.py