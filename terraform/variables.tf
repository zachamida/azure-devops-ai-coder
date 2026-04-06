variable "location" {
  description = "Azure region for all resources"
  type        = string
  default     = "canadaeast"
}

variable "environment" {
  description = "Environment suffix (Dev, Staging, Prod)"
  type        = string
  default     = "Dev"

  validation {
    condition     = length("aicoder${replace(var.environment, "-", "")}storage") <= 24
    error_message = "Environment name is too long. The resulting storage account name must be <= 24 characters."
  }
}

variable "azure_openai_endpoint" {
  description = "Azure OpenAI endpoint URL (e.g., https://xxx.openai.azure.com/)"
  type        = string
  sensitive   = true
}

variable "azure_openai_key" {
  description = "Azure OpenAI API key"
  type        = string
  sensitive   = true
}

variable "azure_openai_deployment" {
  description = "Azure OpenAI deployment name (e.g., gpt-4o)"
  type        = string
  default     = "gpt-4o-mini"
}

variable "azure_devops_org" {
  description = "Azure DevOps organization name"
  type        = string
}

variable "azure_devops_pat" {
  description = "Azure DevOps Personal Access Token with code read/write permissions"
  type        = string
  sensitive   = true
}

variable "container_image" {
  description = "Container image URL (e.g., containername.azurecr.io/ai-coder:latest)"
  type        = string
}

variable "acr_server" {
  description = "Azure Container Registry server"
  type        = string
}

variable "acr_username" {
  description = "Azure Container Registry username"
  type        = string
}

variable "acr_password" {
  description = "Azure Container Registry password"
  type        = string
  sensitive   = true
}

variable "webhook_secret" {
  description = "Secret for webhook signature verification (optional)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "project_repo_map" {
  description = "JSON map of Azure DevOps project names to repository URLs"
  type        = string
  default     = "{\"project_name\": \"https://dev.azure.com/org_name/project_name/_git/Test_Project\"}"
}

variable "log_analytics_workspace_name" {
  description = "Log Analytics workspace name"
  type        = string
  default     = "ai-coder-logs"
}
