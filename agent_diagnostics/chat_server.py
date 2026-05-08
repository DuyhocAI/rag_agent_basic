# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parent
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

# -*- coding: utf-8 -*-
import socket
import threading
import sys
from logging_config import setup_logger
import fix_encoding  # Fix encoding

logger = setup_logger('SERVER', 'server.log')

class ChatServer:
    def __init__(self, host='127.0.0.1', port=5555):
        self.host = host
        self.port = port
        self.clients = []
        self.server_socket = None
        logger.info(f"Khoi tao ChatServer tai {host}:{port}")
    
    def broadcast(self, message, sender_socket=None):
        logger.info(f"Broadcast: {message[:50]}...")
        disconnected_clients = []
        
        for client in self.clients:
            if client != sender_socket:
                try:
                    client.send(message.encode('utf-8'))
                except Exception as e:
                    logger.error(f"Loi gui: {e}")
                    disconnected_clients.append(client)
        
        for client in disconnected_clients:
            self.remove_client(client)
    
    def handle_client(self, client_socket, address):
        logger.info(f"Client ket noi: {address}")
        
        try:
            while True:
                message = client_socket.recv(1024).decode('utf-8')
                if not message:
                    logger.warning(f"Client {address} ngat ket noi")
                    break
                
                logger.info(f"Nhan tu {address}: {message}")
                self.broadcast(f"{address[0]}:{address[1]} - {message}", client_socket)
        
        except Exception as e:
            logger.error(f"Loi xu ly {address}: {e}")
        
        finally:
            self.remove_client(client_socket)
            logger.info(f"Dong ket noi {address}")
    
    def remove_client(self, client_socket):
        if client_socket in self.clients:
            self.clients.remove(client_socket)
            try:
                client_socket.close()
                logger.info(f"Xoa client. Con lai: {len(self.clients)}")
            except:
                pass
    
    def start(self):
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(5)
            
            logger.info(f"Server lang nghe tai {self.host}:{self.port}")
            print(f"[CHAT SERVER] Chay tai {self.host}:{self.port}")
            print("[CHAT SERVER] Nhan Ctrl+C de dung")
            
            while True:
                client_socket, address = self.server_socket.accept()
                self.clients.append(client_socket)
                logger.info(f"Chap nhan {address}. Tong: {len(self.clients)}")
                
                client_thread = threading.Thread(
                    target=self.handle_client,
                    args=(client_socket, address)
                )
                client_thread.daemon = True
                client_thread.start()
        
        except KeyboardInterrupt:
            logger.info("Dung server")
            print("\n[CHAT SERVER] Dang dung...")
        
        except Exception as e:
            logger.error(f"Loi: {e}", exc_info=True)
        
        finally:
            self.stop()
    
    def stop(self):
        logger.info("Dung server")
        
        for client in self.clients[:]:
            try:
                client.close()
            except:
                pass
        
        if self.server_socket:
            try:
                self.server_socket.close()
                logger.info("Dong server socket")
            except:
                pass
        
        logger.info("="*60)
        logger.info("KET THUC PHIEN")
        logger.info("="*60)
        print("[CHAT SERVER] Da dung")

if __name__ == "__main__":
    server = ChatServer()
    server.start()
