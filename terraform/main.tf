resource "azurerm_resource_group" "main" {
  name     = "ai-coder-rg"
  location = var.location
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = var.log_analytics_workspace_name
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

resource "azurerm_storage_account" "main" {
  name                     = "aicoder${lower(replace(var.environment, "-", ""))}storage"
  resource_group_name       = azurerm_resource_group.main.name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "LRS"

  queue_properties {
    logging {
      delete                = true
      read                 = true
      write                = true
      version              = "1.0"
      retention_policy_days = 7
    }

    hour_metrics {
      enabled               = true
      version               = "1.0"
      retention_policy_days = 7
    }

    minute_metrics {
      enabled               = true
      version               = "1.0"
      retention_policy_days = 7
    }
  }
}

resource "azurerm_storage_queue" "main" {
  name                 = "ai-coder-tasks"
  storage_account_name = azurerm_storage_account.main.name
}

resource "azurerm_storage_queue" "dead_letter" {
  name                 = "ai-coder-tasks-dlq"
  storage_account_name = azurerm_storage_account.main.name
}

resource "azurerm_user_assigned_identity" "main" {
  name                = "ai-coder-identity"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
}

resource "azurerm_container_app_environment" "main" {
  name                       = "ai-coder-env"
  resource_group_name         = azurerm_resource_group.main.name
  location                   = var.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
}

resource "azurerm_container_app" "main" {
  name                         = "ai-coder"
  resource_group_name           = azurerm_resource_group.main.name
  container_app_environment_id   = azurerm_container_app_environment.main.id
  revision_mode                = "Single"

  secret {
    name  = "azure-openai-endpoint"
    value = var.azure_openai_endpoint
  }

  secret {
    name  = "azure-openai-key"
    value = var.azure_openai_key
  }

  secret {
    name  = "azure-openai-deployment"
    value = var.azure_openai_deployment
  }

  secret {
    name  = "azure-devops-pat"
    value = var.azure_devops_pat
  }

  secret {
    name  = "azure-devops-org"
    value = var.azure_devops_org
  }

  secret {
    name  = "storage-connection-string"
    value = azurerm_storage_account.main.primary_connection_string
  }

  secret {
    name  = "project-repo-map"
    value = var.project_repo_map
  }

  dynamic "secret" {
    for_each = var.webhook_secret != "" ? [1] : []
    content {
      name  = "webhook-secret"
      value = var.webhook_secret
    }
  }

  dynamic "secret" {
    for_each = var.acr_password != "" ? [1] : []
    content {
      name  = "acr-password"
      value = var.acr_password
    }
  }

  template {
    min_replicas = 1
    max_replicas = 5

    container {
      name   = "ai-coder"
      image  = var.container_image
      cpu    = "2"
      memory = "4Gi"

      env {
        name  = "AZURE_OPENAI_ENDPOINT"
        secret_name = "azure-openai-endpoint"
      }

      env {
        name  = "AZURE_OPENAI_KEY"
        secret_name = "azure-openai-key"
      }

      env {
        name  = "AZURE_OPENAI_DEPLOYMENT"
        secret_name = "azure-openai-deployment"
      }

      env {
        name  = "AZURE_DEVOPS_PAT"
        secret_name = "azure-devops-pat"
      }

      env {
        name  = "AZURE_DEVOPS_ORG"
        secret_name = "azure-devops-org"
      }

      env {
        name  = "STORAGE_CONNECTION_STRING"
        secret_name = "storage-connection-string"
      }

      env {
        name  = "PROJECT_REPO_MAP"
        secret_name = "project-repo-map"
      }

      env {
        name  = "QUEUE_NAME"
        value = "ai-coder-tasks"
      }

      env {
        name  = "DEAD_LETTER_QUEUE_NAME"
        value = "ai-coder-tasks-dlq"
      }

      env {
        name  = "MAX_RETRIES"
        value = "3"
      }
    }
  }

  ingress {
    external_enabled   = true
    target_port        = 8080
    transport          = "http"

    traffic_weight {
      latest_revision = true
      percentage     = 100
    }
  }

  dynamic "registry" {
    for_each = var.acr_password != "" ? [1] : []
    content {
      server               = var.acr_server
      username             = var.acr_username
      password_secret_name = "acr-password"
    }
  }
}
