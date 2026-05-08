# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parent
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

"""
migrate_memory.py - Migrate existing memory DB to optimized version
Run once to upgrade: python migrate_memory.py
"""

import sqlite3
import sys
from pathlib import Path

def migrate_to_fts5(db_path: str):
    """Add FTS5 indexes to existing database."""
    print(f"Migrating {db_path} to FTS5...")
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    try:
        # Check if already migrated
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='episodes_fts'"
        )
        if cursor.fetchone():
            print("✓ Already migrated to FTS5")
            return
        
        print("Creating FTS5 tables...")
        conn.executescript("""
            -- Episodes FTS
            CREATE VIRTUAL TABLE episodes_fts USING fts5(
                summary, tags, content='episodes', content_rowid='id'
            );
            
            INSERT INTO episodes_fts(rowid, summary, tags)
            SELECT id, summary, COALESCE(tags, '') FROM episodes;
            
            CREATE TRIGGER episodes_ai AFTER INSERT ON episodes BEGIN
                INSERT INTO episodes_fts(rowid, summary, tags)
                VALUES (new.id, new.summary, COALESCE(new.tags, ''));
            END;
            
            CREATE TRIGGER episodes_ad AFTER DELETE ON episodes BEGIN
                DELETE FROM episodes_fts WHERE rowid = old.id;
            END;
            
            CREATE TRIGGER episodes_au AFTER UPDATE ON episodes BEGIN
                UPDATE episodes_fts SET summary=new.summary, tags=COALESCE(new.tags, '')
                WHERE rowid=new.id;
            END;
            
            -- Facts FTS
            CREATE VIRTUAL TABLE facts_fts USING fts5(
                key, value, category, content='facts', content_rowid='id'
            );
            
            INSERT INTO facts_fts(rowid, key, value, category)
            SELECT id, key, value, COALESCE(category, 'general') FROM facts;
            
            CREATE TRIGGER facts_ai AFTER INSERT ON facts BEGIN
                INSERT INTO facts_fts(rowid, key, value, category)
                VALUES (new.id, new.key, new.value, COALESCE(new.category, 'general'));
            END;
            
            CREATE TRIGGER facts_ad AFTER DELETE ON facts BEGIN
                DELETE FROM facts_fts WHERE rowid = old.id;
            END;
            
            CREATE TRIGGER facts_au AFTER UPDATE ON facts BEGIN
                UPDATE facts_fts SET key=new.key, value=new.value, 
                       category=COALESCE(new.category, 'general')
                WHERE rowid=new.id;
            END;
        """)
        
        conn.commit()
        print("✓ FTS5 migration complete")
        
        # Optimize
        print("Optimizing database...")
        conn.execute("PRAGMA optimize")
        conn.execute("ANALYZE")
        print("✓ Optimization complete")
        
        # Stats
        ep_count = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        fact_count = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        print(f"\n✓ Migration successful!")
        print(f"  Episodes indexed: {ep_count}")
        print(f"  Facts indexed: {fact_count}")
        
    except Exception as e:
        print(f"✗ Migration failed: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    db_path = Path("C:/Bao_Duy/rag_agent/agent_memory.db")
    
    if not db_path.exists():
        print(f"✗ Database not found: {db_path}")
        print("  Run server.py first to create the database")
        sys.exit(1)
    
    # Backup first
    backup_path = db_path.with_suffix('.db.backup')
    print(f"Creating backup: {backup_path}")
    import shutil
    shutil.copy2(db_path, backup_path)
    print("✓ Backup created")
    
    migrate_to_fts5(str(db_path))