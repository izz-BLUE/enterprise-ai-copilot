CREATE TABLE app_user (
    user_id VARCHAR(64) PRIMARY KEY,
    username VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    employee_id VARCHAR(64) NULL,
    display_name VARCHAR(120) NOT NULL,
    role VARCHAR(16) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT ck_app_user_role
        CHECK (role IN ('EMPLOYEE', 'ADMIN')),
    CONSTRAINT ck_app_user_identity_shape
        CHECK (
            (role = 'ADMIN' AND employee_id IS NULL)
            OR
            (role = 'EMPLOYEE' AND employee_id IS NOT NULL)
        ),
    CONSTRAINT fk_app_user_employee
        FOREIGN KEY (employee_id)
        REFERENCES leave_account(employee_id)
);

CREATE UNIQUE INDEX uk_app_user_employee_id
    ON app_user(employee_id)
    WHERE employee_id IS NOT NULL;
