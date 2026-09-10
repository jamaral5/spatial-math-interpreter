<#
.SYNOPSIS
    PowerShell equivalents of the .sh scripts in this folder, for working from a
    normal Windows terminal instead of Git Bash.

.DESCRIPTION
    Dot-source this file to load the three commands:

        . .\scripts\Spatial-Math.ps1

    then:

        Get-SpatialMathModels            # which Claude models this account can invoke
        Get-SpatialMathApiKey            # the API key value, to paste into Unity
        Invoke-SpatialMathSmokeTest      # end-to-end check against the deployed stack

    All three are thin wrappers over the AWS CLI, so they need `aws configure` to have
    been run first. Nothing here is destructive - they only read.
#>

$script:DefaultStack  = 'spatial-math-interpret'
$script:DefaultRegion = 'us-east-1'

function Get-SpatialMathModels {
    <#
    .SYNOPSIS
        Lists the Anthropic models this account can actually invoke in a region.
    .DESCRIPTION
        RUN THIS BEFORE THE FIRST DEPLOY, to get the exact id for samconfig.toml.

        Know what it proves. This lists the models the region OFFERS. AWS retired the
        old Model access page - serverless foundation models are enabled automatically
        on first invocation - so appearing here no longer means your account can invoke
        it. The only real proof is invoking it: send one message in the Bedrock
        Playground first.

        A model id your account cannot invoke fails at REQUEST time with a 404 or 403,
        not at deploy time, so the stack comes up healthy and then every click fails.
    #>
    [CmdletBinding()]
    param([string]$Region = $script:DefaultRegion)

    Write-Host "Foundation models (on-demand) in $Region :" -ForegroundColor Cyan
    aws bedrock list-foundation-models --region $Region --by-provider anthropic `
        --query 'modelSummaries[].modelId' --output table

    Write-Host ''
    Write-Host "Inference profiles (cross-region) in $Region :" -ForegroundColor Cyan
    # Cross-region profiles carry a regional prefix such as us. or eu. and route to
    # whichever region has capacity. Prefer one of these if the plain id is absent.
    aws bedrock list-inference-profiles --region $Region `
        --query 'inferenceProfileSummaries[?contains(inferenceProfileId, `anthropic`)].inferenceProfileId' `
        --output table

    Write-Host ''
    Write-Host 'Put the id you want into samconfig.toml as ModelId, then: sam deploy'
}

function Get-SpatialMathApiKey {
    <#
    .SYNOPSIS
        Prints the API key value for the deployed stack.
    .DESCRIPTION
        The key is deliberately NOT a CloudFormation output - outputs are kept in stack
        history and visible to anyone with read access, and this key maps directly to a
        Bedrock bill. Paste the result into the Math Interpreter Client component in
        Unity, and do not commit it.
    #>
    [CmdletBinding()]
    param(
        [string]$StackName = $script:DefaultStack,
        [string]$Region    = $script:DefaultRegion
    )

    $keyId = aws cloudformation describe-stacks --stack-name $StackName --region $Region `
        --query "Stacks[0].Outputs[?OutputKey=='ApiKeyId'].OutputValue" --output text

    if ([string]::IsNullOrWhiteSpace($keyId) -or $keyId -eq 'None') {
        Write-Error "Could not find ApiKeyId on stack '$StackName'. Has it been deployed?"
        return
    }

    aws apigateway get-api-key --api-key $keyId --include-value --region $Region `
        --query 'value' --output text
}

function Invoke-SpatialMathSmokeTest {
    <#
    .SYNOPSIS
        End-to-end check against the deployed stack.
    .DESCRIPTION
        Sends the saddle example and prints the response. Run it TWICE: the first call
        should return "cached": false and the second "cached": true. That second run is
        the real test - it proves the DynamoDB layer works, and every cache hit is a
        Bedrock call not paid for.
    #>
    [CmdletBinding()]
    param(
        [string]$StackName = $script:DefaultStack,
        [string]$Region    = $script:DefaultRegion
    )

    $endpoint = aws cloudformation describe-stacks --stack-name $StackName --region $Region `
        --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" --output text

    if ([string]::IsNullOrWhiteSpace($endpoint) -or $endpoint -eq 'None') {
        Write-Error "Could not find ApiEndpoint on stack '$StackName'. Has it been deployed?"
        return
    }

    $key     = Get-SpatialMathApiKey -StackName $StackName -Region $Region
    $payload = Join-Path $PSScriptRoot '..\contracts\examples\saddle-on-a-slope.json'
    $body    = Get-Content -Raw -Path $payload

    Write-Host "POST $endpoint" -ForegroundColor Cyan
    Write-Host ''

    try {
        # -SkipHttpErrorCheck so a 4xx/5xx still prints the JSON body: this backend
        # returns its errors in the same shape as its successes, and the message is
        # the useful part.
        $response = Invoke-RestMethod -Method Post -Uri $endpoint -Body $body `
            -ContentType 'application/json' `
            -Headers @{ 'x-api-key' = $key } `
            -SkipHttpErrorCheck
        $response | ConvertTo-Json -Depth 5
    }
    catch {
        Write-Error $_.Exception.Message
    }
}
