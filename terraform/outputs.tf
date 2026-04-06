output "container_app_fqdn" {
  description = "Fully qualified domain name of the Container App"
  value       = azurerm_container_app.main.ingress[0].fqdn
}

output "webhook_url" {
  description = "Webhook endpoint URL for Azure DevOps Service Hook"
  value       = "https://${azurerm_container_app.main.ingress[0].fqdn}/api/webhook"
}

output "storage_account_name" {
  description = "Storage account name for queue"
  value       = azurerm_storage_account.main.name
}

output "queue_name" {
  description = "Main task queue name"
  value       = azurerm_storage_queue.main.name
}

output "dead_letter_queue_name" {
  description = "Dead letter queue name for failed tasks"
  value       = azurerm_storage_queue.dead_letter.name
}

output "resource_group_name" {
  description = "Resource group name"
  value       = azurerm_resource_group.main.name
}

output "container_app_name" {
  description = "Container App name"
  value       = azurerm_container_app.main.name
}

output "user_assigned_identity_client_id" {
  description = "Client ID of the user-assigned managed identity"
  value       = azurerm_user_assigned_identity.main.client_id
}
