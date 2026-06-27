output "dr_node_public_ip" {
  description = "The public IP address of the newly provisioned Disaster Recovery node"
  value       = aws_instance.dr_standby_node.public_ip
}

output "dr_node_ssh_command" {
  description = "Command to SSH into the DR node"
  value       = "ssh -i ~/.ssh/${var.ssh_key_name}.pem ubuntu@${aws_instance.dr_standby_node.public_ip}"
}
