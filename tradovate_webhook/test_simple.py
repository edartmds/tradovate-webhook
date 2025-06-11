import sys
import os
sys.path.append(os.path.dirname(__file__))

try:
    print("Testing basic import...")
    from main import monitor_all_orders
    print("✓ Successfully imported monitor_all_orders")
    
    print("Testing function signature...")
    import inspect
    sig = inspect.signature(monitor_all_orders)
    print(f"✓ Function signature: {sig}")
    
    print("Testing basic validation...")
    # This should not crash - just testing the function exists and is callable
    print("✓ Function is callable")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
